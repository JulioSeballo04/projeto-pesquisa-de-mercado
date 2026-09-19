"""Linha de comando: orquestra busca -> triagem -> proposta -> PDF -> planilha."""

from __future__ import annotations

import argparse
import dataclasses
import logging
import sys
from datetime import datetime
from pathlib import Path

from .aisa_client import AisaClient, AisaError
from .config import Config, ConfigError
from .demo_data import ITENS_DEMO
from .models import Lead
from .pdf_export import gerar_pdf
from .places import MOTIVOS_LEGIVEIS, buscar_leads, resumo_por_motivo, slug
from .proposal import gerar_proposta
from .relatorio import salvar_csv

log = logging.getLogger("prospector")

# Erros de conta/rota: se acontecerem, todas as próximas chamadas falhariam do mesmo jeito.
ERROS_FATAIS = (401, 402, 403, 404)


def criar_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="main.py",
        description="Encontra negócios locais bem avaliados e sem site próprio e gera propostas comerciais em PDF.",
    )
    p.add_argument("-c", "--categoria", action="append", metavar="TERMO",
                   help="Categoria a pesquisar (pode repetir: -c padaria -c barbearia). Padrão: CATEGORIES do .env")
    p.add_argument("--min-nota", type=float, help="Nota mínima no Google (padrão: MIN_RATING do .env)")
    p.add_argument("--min-avaliacoes", type=int, help="Mínimo de avaliações (padrão: MIN_REVIEWS do .env)")
    p.add_argument("--profundidade", type=int, help="Resultados por categoria (padrão: SEARCH_DEPTH do .env)")
    p.add_argument("--limite", type=int, default=10, help="Máximo de propostas geradas por execução (padrão: 10)")
    p.add_argument("--permitir-sem-telefone", action="store_true", help="Não descartar empresas sem telefone")
    p.add_argument("--dry-run", action="store_true", help="Só busca e tria os leads; não usa o LLM nem gera PDFs")
    p.add_argument("--sem-llm", action="store_true", help="Gera as propostas com o texto padrão, sem chamar o LLM")
    p.add_argument("--demo", action="store_true", help="Usa dados fictícios (não chama o Google Maps)")
    p.add_argument("-y", "--yes", action="store_true", help="Não pedir confirmação antes de gastar créditos")
    p.add_argument("--saida", type=Path, help="Pasta de saída (padrão: OUTPUT_DIR do .env)")
    return p


def _confirmar(plano: str, sem_perguntar: bool) -> bool:
    print(plano)
    if sem_perguntar:
        return True
    if not sys.stdin.isatty():
        print("Execução sem terminal interativo: use --yes para confirmar.")
        return False
    return input("Continuar? [s/N] ").strip().lower() in ("s", "sim", "y", "yes")


def _processar_propostas(cliente, lead: Lead, cfg: Config, pasta: Path, usar_llm: bool) -> None:
    proposta = gerar_proposta(cliente, lead.business, cfg, usar_llm=usar_llm)
    arquivo = pasta / "propostas" / f"{slug(lead.business.name)}-{slug(lead.business.place_id)[-6:]}.pdf"
    gerar_pdf(lead.business, proposta, cfg, arquivo)
    lead.proposal_pdf = str(arquivo)
    lead.proposal_source = proposta.source


def main(argv: list[str] | None = None) -> int:
    args = criar_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)

    # A chave só é necessária se algo for chamar a AIsa: busca real ou LLM.
    usa_llm = not args.sem_llm and not args.dry_run
    precisa_chave = (not args.demo) or usa_llm
    try:
        cfg = Config.from_env(require_api_key=precisa_chave)
    except ConfigError as erro:
        print(f"Erro de configuração: {erro}")
        return 1

    ajustes = {}
    if args.min_nota is not None:
        ajustes["min_rating"] = args.min_nota
    if args.min_avaliacoes is not None:
        ajustes["min_reviews"] = args.min_avaliacoes
    if args.saida is not None:
        ajustes["output_dir"] = args.saida.resolve()
    if ajustes:
        cfg = dataclasses.replace(cfg, **ajustes)
    if not 0 <= cfg.min_rating <= 5:
        print("Erro: --min-nota deve estar entre 0 e 5.")
        return 1
    profundidade = args.profundidade or cfg.search_depth
    if not 1 <= profundidade <= 700:
        print("Erro: --profundidade deve estar entre 1 e 700.")
        return 1
    if args.limite < 1:
        print("Erro: --limite deve ser pelo menos 1.")
        return 1

    categorias = args.categoria or (list(ITENS_DEMO) if args.demo else list(cfg.categories))
    cliente = AisaClient(cfg.api_key, cfg.base_url) if cfg.api_key else None

    modo = "DEMONSTRAÇÃO (dados fictícios)" if args.demo else f"busca real em {cfg.city_name}"
    plano = (
        f"\nModo: {modo}\n"
        f"Categorias: {', '.join(categorias)}\n"
        f"Filtros: nota >= {cfg.min_rating:.1f}, >= {cfg.min_reviews} avaliações, sem site próprio, com telefone\n"
    )
    if not args.demo:
        plano += f"Custo: {len(categorias)} busca(s) no Google Maps (até {profundidade} resultados cada)"
        plano += "" if not usa_llm else f" + até {args.limite} chamada(s) ao LLM"
        plano += " — consome créditos da AIsa.\n"
    if not _confirmar(plano, sem_perguntar=args.yes or args.demo):
        print("Cancelado.")
        return 0

    pasta = cfg.output_dir / datetime.now().strftime("%Y-%m-%d_%H%M%S")
    leads: list[Lead] = []
    codigo = 0
    try:
        leads = buscar_leads(
            cliente, cfg, categorias, profundidade,
            itens_demo=ITENS_DEMO if args.demo else None,
            permitir_sem_telefone=args.permitir_sem_telefone,
        )
        aprovados = [l for l in leads if l.approved]

        print("\nTriagem:")
        for motivo, quantidade in sorted(resumo_por_motivo(leads).items(), key=lambda x: -x[1]):
            print(f"  {quantidade:3d}  {MOTIVOS_LEGIVEIS.get(motivo, motivo)}")

        if not aprovados:
            print("\nNenhum lead aprovado. Tente outras categorias, reduzir --min-nota ou aumentar --profundidade.")
            codigo = 2
        elif args.dry_run:
            print(f"\n{len(aprovados)} lead(s) aprovado(s) (dry-run: nenhuma proposta gerada):")
            for l in aprovados:
                print(f"  - {l.business.name} | nota {l.business.rating} ({l.business.reviews}) | {l.business.phone or 'sem telefone'}")
        else:
            selecionados = aprovados[: args.limite]
            if len(aprovados) > len(selecionados):
                print(f"\n{len(aprovados)} aprovados; gerando as {len(selecionados)} primeiras (use --limite para mudar).")
            print("\nGerando propostas:")
            for lead in selecionados:
                print(f"  - {lead.business.name}")
                try:
                    _processar_propostas(cliente, lead, cfg, pasta, usa_llm)
                except AisaError as erro:
                    if erro.status in ERROS_FATAIS:
                        raise
                    lead.error = str(erro)
                    print(f"    ! falhou: {erro}")
                except Exception as erro:  # um lead com problema não pode derrubar os outros
                    lead.error = f"{type(erro).__name__}: {erro}"
                    print(f"    ! falhou: {lead.error}")
    except AisaError as erro:
        print(f"\nErro na AIsa: {erro}")
        codigo = 1
    finally:
        if leads:
            csv_path = salvar_csv(leads, pasta / "leads.csv")
            print(f"\nPlanilha de leads: {csv_path}")

    gerados = [l for l in leads if l.proposal_pdf]
    if gerados:
        print(f"{len(gerados)} proposta(s) em: {pasta / 'propostas'}")
        print("Revise cada PDF antes de enviar: o texto foi gerado automaticamente a partir de dados públicos.")
    return codigo


if __name__ == "__main__":
    sys.exit(main())
