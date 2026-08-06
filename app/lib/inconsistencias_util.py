"""
Helper compartilhado para gravar inconsistências AGRUPADAS (pedido do usuário em 06/08/2026: "um mesmo
erro pode se repetir, é melhor que ele agrupe") em vez de uma linha por item — e gravar o vínculo com os
itens de NF por trás de cada grupo (tabela inconsistencia_itens, ver sql/008), usado pela Planilha de
Entrada/Saída para sinalizar diretamente na grade quais linhas têm inconsistência pendente.

Quem gera a inconsistência monta um dict {chave_agrupamento: {"ncm": ..., "cfop": ..., "descricao": ...,
"item_ids": [...]}} e chama `gravar_grupos`. A função: (1) insere um resumo por grupo em `inconsistencias`
em lote, (2) busca de volta os ids gerados (bigserial, não dá pra saber direto do INSERT em lote via
pandas.to_sql) pela chave_agrupamento, (3) grava o vínculo grupo->itens em `inconsistencia_itens`, também
em lote — mesma técnica de bulk insert já usada em app/lib/importacao.py, evita 1 INSERT por item.
"""
import pandas as pd
from sqlalchemy import text


def gravar_grupos(session, competencia_id: int, tipo: str, grupos: dict) -> int:
    """`grupos`: chave_agrupamento -> {"ncm": str|None, "cfop": int|None, "descricao": str,
    "item_ids": list[int]}. Retorna quantos GRUPOS foram gravados (não quantos itens).

    Pré-requisito: quem chama já deve ter apagado as inconsistências antigas deste `tipo`/competência
    antes (e dado commit) — esta função só insere, não limpa nada."""
    if not grupos:
        return 0

    linhas = [{
        "competencia_id": competencia_id, "tipo": tipo, "ncm": g.get("ncm"), "cfop": g.get("cfop"),
        "nf_item_id": (g["item_ids"][0] if g["item_ids"] else None),
        "descricao": g["descricao"], "chave_agrupamento": chave, "quantidade": len(g["item_ids"]),
    } for chave, g in grupos.items()]

    df = pd.DataFrame(linhas, columns=[
        "competencia_id", "tipo", "ncm", "cfop", "nf_item_id", "descricao", "chave_agrupamento", "quantidade",
    ])
    df.to_sql("inconsistencias", session.bind, if_exists="append", index=False, method="multi", chunksize=500)

    # busca de volta os ids recém-gerados pela chave — não tem como saber o id direto do to_sql (bigserial)
    ids = session.execute(text("""
        select id, chave_agrupamento from inconsistencias
        where competencia_id = :cid and tipo = :tipo
    """), {"cid": competencia_id, "tipo": tipo}).mappings().all()
    id_por_chave = {r["chave_agrupamento"]: r["id"] for r in ids}

    vinculos = [
        {"inconsistencia_id": id_por_chave[chave], "nf_item_id": item_id}
        for chave, g in grupos.items() if chave in id_por_chave
        for item_id in g["item_ids"]
    ]
    if vinculos:
        df_v = pd.DataFrame(vinculos, columns=["inconsistencia_id", "nf_item_id"])
        df_v.to_sql("inconsistencia_itens", session.bind, if_exists="append", index=False,
                    method="multi", chunksize=1000)

    return len(grupos)
