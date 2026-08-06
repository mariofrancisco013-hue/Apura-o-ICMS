"""
Preenche produto_codigo/produto_descricao dos itens de Entrada/Saída JÁ importados antes desta feature
existir (06/08/2026) — separa a célula "produto" (formato Winthor "<código> - <descrição>") nas duas
colunas novas. Itens importados DAQUI PRA FRENTE já vêm com essas colunas preenchidas direto pela
importação (app/lib/importacao.py) — este script é só para o histórico.

Por que rodar local e não colar como UPDATE no SQL Editor do Supabase: um UPDATE direto na tabela inteira
(dezenas de milhares de linhas) estourou o timeout do painel do Supabase ("upstream timeout" — limite do
proxy do painel, não do Postgres). Rodando daqui, com sua DATABASE_URL, não tem esse limite.

Uso (rode no SEU computador, com DATABASE_URL configurado — mesmo jeito do scripts/seed_ncm.py):
    python scripts/backfill_produto_codigo.py

Pode rodar de novo sem medo: só atualiza itens com produto_codigo ainda vazio (não mexe em nada que você
já tenha editado manualmente na grade).
"""
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.lib.db import get_session  # noqa: E402
from app.lib.importacao import _dividir_codigo_descricao  # noqa: E402

TABELA_TEMP = "produto_backfill_tmp"


def main():
    session = get_session()
    rows = session.execute(text("""
        select id, produto from notas_fiscais_itens
        where produto_codigo is null and produto is not null
    """)).mappings().all()

    if not rows:
        print("Nada para atualizar — todos os itens já têm produto_codigo/produto_descricao preenchidos "
              "(ou não têm 'produto' registrado).")
        return

    df = pd.DataFrame(rows, columns=["id", "produto"])
    splits = df["produto"].apply(_dividir_codigo_descricao)
    df["produto_codigo"] = splits.apply(lambda par: par[0])
    df["produto_descricao"] = splits.apply(lambda par: par[1])
    df = df[["id", "produto_codigo", "produto_descricao"]]

    # usa uma tabela temporária "de verdade" (não `create temporary table`) porque to_sql via pandas pode
    # pegar outra conexão do pool — uma TEMP TABLE só existiria na conexão que a criou.
    session.execute(text(f"drop table if exists {TABELA_TEMP}"))
    session.commit()
    session.execute(text(
        f"create table {TABELA_TEMP} (id bigint primary key, produto_codigo text, produto_descricao text)"
    ))
    session.commit()

    df.to_sql(TABELA_TEMP, session.bind, if_exists="append", index=False, method="multi", chunksize=1000)

    session.execute(text(f"""
        update notas_fiscais_itens n
        set produto_codigo = t.produto_codigo, produto_descricao = t.produto_descricao
        from {TABELA_TEMP} t
        where t.id = n.id
    """))
    session.commit()

    session.execute(text(f"drop table if exists {TABELA_TEMP}"))
    session.commit()

    print(f"{len(df)} item(ns) atualizado(s) com produto_codigo/produto_descricao.")


if __name__ == "__main__":
    main()
