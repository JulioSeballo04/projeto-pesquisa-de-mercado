"""Geração do PDF da proposta com fpdf2 (sem depender de fontes externas)."""

from __future__ import annotations

import re
import unicodedata
from datetime import date, timedelta
from pathlib import Path

from fpdf import FPDF
from fpdf.enums import XPos, YPos
from fpdf.fonts import FontFace

from .config import Config
from .models import Business, Proposal
from .proposal import tabela_investimento

# Cores (RGB)
VERDE = (15, 110, 86)
TEXTO = (34, 32, 27)
CINZA = (107, 106, 99)
FUNDO_SUAVE = (240, 246, 243)
LINHA = (218, 215, 206)

# As fontes internas do PDF só cobrem Latin-1 — os acentos do português estão nele, mas
# pontuação "elegante" (travessão, aspas curvas, marcadores) que os LLMs adoram usar, não.
_SUBSTITUICOES = {
    "–": "-", "—": "-", "−": "-",
    "‘": "'", "’": "'", "‚": ",",
    "“": '"', "”": '"', "„": '"',
    "•": "-", "●": "-", "▪": "-",
    "…": "...", " ": " ", " ": " ",
    "→": "->", "·": "|",
}


def texto_seguro(valor) -> str:
    """Troca pontuação especial e remove o que o PDF não consegue desenhar (ex.: emojis)."""
    texto = unicodedata.normalize("NFC", str(valor))
    for origem, destino in _SUBSTITUICOES.items():
        texto = texto.replace(origem, destino)
    texto = texto.encode("latin-1", "ignore").decode("latin-1")
    # Tirar um emoji entre dois espaços deixaria um espaço duplo no PDF.
    return re.sub(r"[ 	]{2,}", " ", texto)


class PropostaPDF(FPDF):
    def __init__(self, cfg: Config, negocio: Business, hoje: date):
        super().__init__(format="A4", unit="mm")
        self.cfg = cfg
        self.negocio = negocio
        self.hoje = hoje
        self.set_margins(18, 18, 18)
        self.set_auto_page_break(auto=True, margin=22)
        self.set_title(texto_seguro(f"Proposta - {negocio.name}"))
        self.set_author(texto_seguro(cfg.company_name))

    # ----- cabeçalho e rodapé (repetem em todas as páginas) -----

    def header(self):
        self.set_fill_color(*VERDE)
        self.rect(0, 0, self.w, 22, style="F")
        self.set_xy(self.l_margin, 7)
        self.set_text_color(255, 255, 255)
        self.set_font("Helvetica", "B", 14)
        self.cell(0, 8, texto_seguro(self.cfg.company_name), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_xy(self.l_margin, 8)
        self.set_font("Helvetica", "", 10)
        self.cell(0, 8, "PROPOSTA COMERCIAL", align="R")
        self.set_y(30)
        self.set_text_color(*TEXTO)

    def footer(self):
        self.set_y(-16)
        self.set_draw_color(*LINHA)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.set_font("Helvetica", "", 7.5)
        self.set_text_color(*CINZA)
        self.ln(1.5)
        aviso = (
            f"Diagnóstico baseado em dados públicos do Google Maps ({self.hoje:%d/%m/%Y}). "
            f"{texto_seguro(self.cfg.company_contact)}"
        )
        self.multi_cell(self.w - self.l_margin - self.r_margin - 18, 3.8, aviso, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_xy(self.w - self.r_margin - 18, self.h - 14.5)
        self.cell(18, 4, f"Pág. {self.page_no()}", align="R")

    # ----- blocos reutilizáveis -----

    def titulo_secao(self, texto: str, altura_minima: float = 48):
        # Evita título "órfão": se não houver espaço para o começo do conteúdo, vai para a próxima página.
        if self.get_y() > self.h - self.b_margin - altura_minima:
            self.add_page()
        self.ln(4)
        self.set_font("Helvetica", "B", 12.5)
        self.set_text_color(*VERDE)
        self.cell(0, 7, texto_seguro(texto), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_draw_color(*VERDE)
        self.line(self.l_margin, self.get_y(), self.l_margin + 18, self.get_y())
        self.ln(2.5)
        self.set_text_color(*TEXTO)

    def paragrafo(self, texto: str, tamanho: float = 10):
        self.set_font("Helvetica", "", tamanho)
        self.set_text_color(*TEXTO)
        self.multi_cell(0, 5.2, texto_seguro(texto), align="L", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.ln(1)

    def marcadores(self, itens: list[str]):
        self.set_font("Helvetica", "", 10)
        self.set_text_color(*TEXTO)
        largura = self.w - self.r_margin - self.l_margin - 5
        for item in itens:
            y = self.get_y()
            self.set_x(self.l_margin)
            self.cell(5, 5.2, "-")
            self.set_xy(self.l_margin + 5, y)
            self.multi_cell(largura, 5.2, texto_seguro(item), align="L", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            self.ln(0.8)

    def tabela(self, linhas: list[tuple[str, str]], larguras: tuple[float, float], cabecalho: tuple[str, str] | None = None):
        estilo_cab = FontFace(emphasis="BOLD", color=(255, 255, 255), fill_color=VERDE)
        self.set_font("Helvetica", "", 9.5)
        self.set_draw_color(*LINHA)
        with self.table(
            col_widths=larguras,
            text_align=("LEFT", "LEFT"),
            line_height=5.4,
            first_row_as_headings=cabecalho is not None,
            headings_style=estilo_cab,
            borders_layout="HORIZONTAL_LINES",
            padding=1.6,
        ) as tabela:
            if cabecalho:
                linha = tabela.row()
                for celula in cabecalho:
                    linha.cell(texto_seguro(celula))
            for chave, valor in linhas:
                linha = tabela.row()
                linha.cell(texto_seguro(chave), style=FontFace(emphasis="BOLD"))
                linha.cell(texto_seguro(valor))
        self.ln(1)


def _texto_avaliacao(n: Business) -> str:
    if n.rating is None:
        return "Sem avaliações"
    nota = f"{n.rating:.1f}".replace(".", ",")
    return f"{nota} de 5 ({n.reviews or 0} avaliações no Google)"


def _texto_redes(n: Business) -> str:
    return ", ".join(n.social_links) if n.social_links else "Não identificadas nos dados públicos"


def _montar_pdf(negocio: Business, proposta: Proposal, cfg: Config, hoje: date | None) -> PropostaPDF:
    hoje = hoje or date.today()
    invest = tabela_investimento(cfg)
    valida_ate = hoje + timedelta(days=cfg.validity_days)

    pdf = PropostaPDF(cfg, negocio, hoje)
    pdf.add_page()

    # Título
    pdf.set_font("Helvetica", "B", 19)
    pdf.set_text_color(*TEXTO)
    pdf.multi_cell(0, 8.5, texto_seguro(f"Site profissional para {negocio.name}"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "", 9.5)
    pdf.set_text_color(*CINZA)
    pdf.cell(
        0, 6,
        f"Preparada em {hoje:%d/%m/%Y}  |  Válida até {valida_ate:%d/%m/%Y}",
        new_x=XPos.LMARGIN, new_y=YPos.NEXT,
    )

    # 1. Diagnóstico
    pdf.titulo_secao("1. Diagnóstico da presença digital")
    pdf.tabela(
        [
            ("Categoria", negocio.category or "-"),
            ("Endereço", negocio.address or "-"),
            ("Avaliação no Google", _texto_avaliacao(negocio)),
            ("Site próprio", "Não encontrado"),
            ("Redes sociais", _texto_redes(negocio)),
        ],
        larguras=(46, 128),
    )
    pdf.ln(1)
    pdf.paragrafo(proposta.summary)

    pdf.set_font("Helvetica", "B", 10.5)
    pdf.cell(0, 6, "Pontos fortes", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.marcadores(proposta.strengths)
    pdf.ln(1)
    pdf.set_font("Helvetica", "B", 10.5)
    pdf.cell(0, 6, "Pontos de atenção", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.marcadores(proposta.weaknesses)

    # 2. Escopo
    pdf.titulo_secao("2. O que vamos entregar")
    pdf.tabela(list(proposta.scope), larguras=(46, 128), cabecalho=("Item", "Descrição"))

    # 3. Investimento
    pdf.titulo_secao("3. Investimento e prazo")
    pdf.tabela(
        [
            ("Investimento à vista", invest["total_texto"]),
            (
                "Parcelado",
                f"{invest['parcelas']}x de {invest['parcela_texto']}" if invest["parcelas"] > 1 else "-",
            ),
            ("Prazo de entrega", f"{invest['prazo_dias']} dias, a partir do recebimento das informações e fotos do negócio"),
            ("Validade da proposta", f"{invest['validade_dias']} dias (até {valida_ate:%d/%m/%Y})"),
        ],
        larguras=(46, 128),
        cabecalho=("Condição", "Detalhe"),
    )
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*CINZA)
    pdf.cell(0, 4.5, "Parcelas arredondadas ao centavo. Hospedagem, domínio e conteúdo adicional: a combinar.",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    # 4. Argumentos
    pdf.titulo_secao("4. Por que faz sentido agora")
    pdf.marcadores(proposta.arguments)

    # Chamada final em destaque (evita ficar "solta" no fim de uma página)
    if pdf.get_y() > pdf.h - 60:
        pdf.add_page()
    pdf.ln(3)
    pdf.set_fill_color(*FUNDO_SUAVE)
    pdf.set_draw_color(*VERDE)
    pdf.set_font("Helvetica", "B", 10.5)
    pdf.set_text_color(*VERDE)
    pdf.multi_cell(0, 6, "Próximo passo", fill=True, new_x=XPos.LMARGIN, new_y=YPos.NEXT, padding=(3, 4, 0, 4))
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(*TEXTO)
    chamada = proposta.closing
    if cfg.company_contact:
        chamada += f"\n{cfg.company_contact}"
    pdf.multi_cell(0, 5.4, texto_seguro(chamada), align="L", fill=True, new_x=XPos.LMARGIN, new_y=YPos.NEXT, padding=(0, 4, 3, 4))

    return pdf


def gerar_pdf(negocio: Business, proposta: Proposal, cfg: Config, destino: Path, hoje: date | None = None) -> Path:
    """Monta o PDF da proposta e o grava em `destino`. Devolve o caminho."""
    pdf = _montar_pdf(negocio, proposta, cfg, hoje)
    destino.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(destino))
    return destino


def gerar_pdf_bytes(negocio: Business, proposta: Proposal, cfg: Config, hoje: date | None = None) -> bytes:
    """Mesmo PDF, mas em memória (usado pelo app web, que não grava arquivos no servidor)."""
    return bytes(_montar_pdf(negocio, proposta, cfg, hoje).output())
