"""
Carrega a tabela de referência de CFOP (data/cfop.csv, extraída da tabela oficial do Winthor em
05/08/2026) no banco, com os ajustes manuais já confirmados contra a Rotina 1025 real de julho/2026.

Uso:
    python scripts/seed_cfop.py

IMPORTANTE — leia antes de rodar em produção:
A classificação automática `is_st_padrao` vem de um regex sobre a descrição do CFOP ("contém SUJEITA A
S.T. / SUBSTITUIÇÃO TRIBUTÁRIA"). O export do Winthor trunca descrições longas, e isso já escondeu a
palavra-chave em pelo menos 2 códigos usados por esta empresa (6108 e 6202) — só descobrimos porque
batemos o cálculo contra a Rotina 1025 real. Antes de considerar a tabela "confiável" para outros CFOPs
que ainda não apareceram nos dados importados, vale revisar a lista completa com o contador.
"""
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.lib.db import get_session  # noqa: E402
from sqlalchemy import text  # noqa: E402

DATA_CSV = Path(__file__).resolve().parent.parent / "data" / "cfop.csv"

# Ajustes manuais confirmados em 05/08/2026 (ver claude/metodologia-icms-normal.md no projeto):
# CFOP -> (is_st_ajuste, regra_especial)
AJUSTES_CONFIRMADOS = {
    6108: (True, None),  # descrição truncada escondia "SUJEITA A S.T." — confirmado batendo Rotina 1025
    6202: (True, "Descrição genérica ('DEV. DE COMPRA PARA COMERCIALIZACAO') não indica ST, mas o "
                 "ICMS deste CFOP entra em '07 - Estorno de Débitos' no livro real — confirmado contra "
                 "Rotina 1025 de julho/2026 (R$ 58,97)."),
    5927: (None, "Regra do usuário (05/08/2026): produtos que geraram crédito e saem sob este CFOP têm "
                 "esse crédito estornado em outro lançamento — o destaque de ICMS nesta operação é válido "
                 "e NÃO deve ser sinalizado como inconsistência, mesmo parecendo uma saída sem débito."),
}


def main():
    with DATA_CSV.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    session = get_session()
    for r in rows:
        codigo = int(r["codigo"])
        ajuste, regra = AJUSTES_CONFIRMADOS.get(codigo, (None, None))
        session.execute(text("""
            insert into cfop (codigo, descricao, is_st_padrao, is_st_ajuste, is_transferencia, regra_especial)
            values (:codigo, :descricao, :is_st_padrao, :is_st_ajuste, :is_transferencia, :regra_especial)
            on conflict (codigo) do update
                set descricao = excluded.descricao,
                    is_st_padrao = excluded.is_st_padrao,
                    is_transferencia = excluded.is_transferencia,
                    updated_at = now()
        """), {
            "codigo": codigo,
            "descricao": r["descricao"],
            "is_st_padrao": r["is_st"] == "True",
            "is_st_ajuste": ajuste,
            "is_transferencia": r["is_transferencia"] == "True",
            "regra_especial": regra,
        })
    session.commit()
    print(f"{len(rows)} códigos de CFOP carregados/atualizados "
          f"({len(AJUSTES_CONFIRMADOS)} com ajuste manual confirmado).")


if __name__ == "__main__":
    main()
