"""Dados FICTÍCIOS no formato do DataForSEO, para testar o fluxo sem gastar créditos.

Todos os nomes trazem "(DEMO)" e os telefones são inválidos de propósito: nada aqui
é uma empresa real. Cada item exercita uma regra de triagem diferente.
"""

from __future__ import annotations


def _item(**campos) -> dict:
    base = {"type": "maps_search", "domain": None, "url": None, "is_claimed": True}
    base.update(campos)
    return base


ITENS_DEMO: dict[str, list[dict]] = {
    "padaria": [
        # Aprovada: nota alta, sem nenhum site.
        _item(
            title="Padaria Pão Quente (DEMO)", category="Padaria", place_id="demo-1",
            address="Rua Exemplo, 100 - Centro, Bragança Paulista - SP, 12900-000",
            phone="(11) 90000-0001", rating={"value": 4.8, "votes_count": 312, "rating_max": 5},
            work_hours={"timetable": {"monday": []}},
        ),
        # Descartada: já tem site próprio.
        _item(
            title="Padaria Trigo Dourado (DEMO)", category="Padaria", place_id="demo-2",
            address="Av. Modelo, 20 - Jardim Europa, Bragança Paulista - SP",
            phone="(11) 90000-0002", rating={"value": 4.9, "votes_count": 540, "rating_max": 5},
            domain="padariatrigodourado.example.com.br", url="https://padariatrigodourado.example.com.br",
        ),
    ],
    "barbearia": [
        # Aprovada: só Instagram cadastrado (não conta como site próprio).
        _item(
            title="Barbearia Navalha Fina (DEMO)", category="Barbearia", place_id="demo-3",
            address="Rua Teste, 55 - Centro, Bragança Paulista - SP",
            phone="(11) 90000-0003", rating={"value": 4.7, "votes_count": 128, "rating_max": 5},
            domain="instagram.com", url="https://www.instagram.com/barbearia_demo",
            work_hours={"timetable": {"monday": []}},
        ),
    ],
    "salão de beleza": [
        # Descartada: nota abaixo do mínimo.
        _item(
            title="Salão Cabelo Vivo (DEMO)", category="Salão de beleza", place_id="demo-4",
            address="Rua das Flores, 7 - Lago do Taboão, Bragança Paulista - SP",
            phone="(11) 90000-0004", rating={"value": 4.2, "votes_count": 90, "rating_max": 5},
        ),
    ],
    "restaurante": [
        # Descartada: sem telefone de contato.
        _item(
            title="Restaurante Sabor da Serra (DEMO)", category="Restaurante", place_id="demo-5",
            address="Estrada Exemplo, km 3 - Bragança Paulista - SP",
            phone=None, rating={"value": 4.9, "votes_count": 210, "rating_max": 5},
        ),
    ],
    "academia": [
        # Descartada: endereço em outra cidade.
        _item(
            title="Academia Corpo em Forma (DEMO)", category="Academia", place_id="demo-6",
            address="Rua Modelo, 300 - Centro, Atibaia - SP",
            phone="(11) 90000-0006", rating={"value": 4.8, "votes_count": 400, "rating_max": 5},
        ),
    ],
}
