"""
Lógica de importação dos relatórios de Entrada/Saída — compartilhada entre scripts/import_relatorios.py
(linha de comando) e a página Streamlit "Importar Relatórios" (upload pelo navegador).

Mapeamento de colunas confirmado em 05/08/2026 (ver claude/metodologia-icms-normal.md no projeto) — os
exports vêm com cabeçalhos genéricos "Coluna1", "Coluna2"... por isso o mapeamento é posicional.

IMPORTANTE (achado de 05/08/2026): o relatório de Saída chega a ~45 mil linhas. A primeira versão deste
módulo inserida linha a linha (um INSERT por linha = ~45 mil idas e voltas ao banco) e travava por muito
tempo em produção. A versão atual monta um DataFrame já no formato da tabela e usa `DataFrame.to_sql(...,
method="multi", chunksize=500)`, que agrupa até 500 linhas por INSERT — poucas dezenas de idas e voltas em
vez de dezenas de milhares.
"""
import json

import pandas as pd
from sqlalchemy import text

COLS_ENTRADA = [
    "parceiro", "nf_numero", "tipo_genero_item", "data_emissao", "data_entrada", "produto", "ncm", "cfop",
    "valor_produto", "_col10", "_col11", "_col12", "_col13", "aliq_fcp", "valor_fcp", "aliq_icms",
    "base_icms", "valor_icms", "_col19", "_col20", "valor_total", "uf", "prazo_dias",
]
COLS_SAIDA = [
    "parceiro", "nf_numero", "tipo_genero_item", "data_emissao", "produto", "ncm", "cfop", "valor_produto",
    "_col9", "_col10", "_col11", "_col12", "_col13", "_col14", "aliq_icms", "base_icms", "valor_icms",
    "_col18", "_col19", "valor_total", "uf", "prazo_dias",
]

# Colunas finais da tabela notas_fiscais_itens que este módulo preenche (na ordem do INSERT via to_sql)
COLS_TABELA = [
    "competencia_id", "tipo_operacao", "parceiro", "nf_numero", "tipo_genero_item",
    "data_emissao", "data_entrada", "produto", "ncm", "cfop", "valor_produto",
    "aliq_fcp", "valor_fcp", "aliq_icms", "base_icms", "valor_icms", "valor_total",
    "uf", "prazo_dias", "colunas_nao_identificadas",
]


def get_or_create_competencia(session, empresa_cnpj, ano, mes, modulo="icms_normal"):
    empresa = session.execute(
        text("select id from empresas where cnpj = :cnpj"), {"cnpj": empresa_cnpj}
    ).fetchone()
    if not empresa:
        raise ValueError(f"Empresa com CNPJ {empresa_cnpj} não encontrada.")
    empresa_id = empresa[0]

    comp = session.execute(text("""
        select id from competencias where empresa_id=:eid and ano=:ano and mes=:mes and modulo=:modulo
    """), {"eid": empresa_id, "ano": ano, "mes": mes, "modulo": modulo}).fetchone()
    if comp:
        return comp[0]

    result = session.execute(text("""
        insert into competencias (empresa_id, ano, mes, modulo, status)
        values (:eid, :ano, :mes, :modulo, 'aberta') returning id
    """), {"eid": empresa_id, "ano": ano, "mes": mes, "modulo": modulo})
    session.commit()
    return result.fetchone()[0]


def checar_duplicacao(session, competencia_id, substituir):
    n = session.execute(
        text("select count(*) from notas_fiscais_itens where competencia_id = :cid"),
        {"cid": competencia_id},
    ).scalar()
    if n and not substituir:
        raise ValueError(
            f"Já existem {n} itens importados para esta competência. Marque/passe --substituir se este é "
            f"um relatório corrigido (evita duplicar por engano)."
        )
    if n and substituir:
        session.execute(text("delete from notas_fiscais_itens where competencia_id = :cid"), {"cid": competencia_id})
        session.execute(text("delete from apuracao_linhas where competencia_id = :cid"), {"cid": competencia_id})
        session.execute(text("delete from inconsistencias where competencia_id = :cid"), {"cid": competencia_id})
        session.commit()
    return n or 0


def _preparar_dataframe(arquivo, tipo_operacao, competencia_id):
    """Lê o .xls e devolve um DataFrame já no formato exato da tabela notas_fiscais_itens, pronto para
    to_sql — nenhum loop linha a linha."""
    cols = COLS_ENTRADA if tipo_operacao == "entrada" else COLS_SAIDA
    df = pd.read_excel(arquivo, sheet_name="Report", header=0, engine="xlrd")
    if len(df.columns) != len(cols):
        raise ValueError(
            f"Arquivo de {tipo_operacao} tem {len(df.columns)} colunas, esperado {len(cols)}. "
            f"O layout do export pode ter mudado — confira antes de importar."
        )
    df.columns = cols

    colunas_extra = [c for c in cols if c.startswith("_")]
    # Monta a partir de uma coluna real primeiro (fixa o número de linhas/índice do DataFrame) — atribuir
    # um escalar (competencia_id, tipo_operacao) direto num DataFrame ainda vazio não funciona: o pandas
    # não tem como saber quantas linhas replicar e a coluna sai inteira NaN (bug encontrado em 05/08/2026).
    out = pd.DataFrame({"parceiro": df["parceiro"]})
    out["competencia_id"] = competencia_id
    out["tipo_operacao"] = tipo_operacao
    out["nf_numero"] = df["nf_numero"].astype(str)
    out["tipo_genero_item"] = df["tipo_genero_item"].astype(str)
    out["data_emissao"] = pd.to_datetime(df["data_emissao"], errors="coerce").dt.date
    out["data_entrada"] = (
        pd.to_datetime(df["data_entrada"], errors="coerce").dt.date if tipo_operacao == "entrada" else None
    )
    out["produto"] = df["produto"]
    out["ncm"] = df["ncm"].astype(str)
    out["cfop"] = df["cfop"].astype(int)
    out["valor_produto"] = df["valor_produto"].fillna(0).astype(float)
    out["aliq_fcp"] = df["aliq_fcp"].fillna(0).astype(float) if "aliq_fcp" in cols else None
    out["valor_fcp"] = df["valor_fcp"].fillna(0).astype(float) if "valor_fcp" in cols else None
    out["aliq_icms"] = df["aliq_icms"].fillna(0).astype(float)
    out["base_icms"] = df["base_icms"].fillna(0).astype(float)
    out["valor_icms"] = df["valor_icms"].fillna(0).astype(float)
    out["valor_total"] = df["valor_total"].fillna(0).astype(float)
    out["uf"] = df["uf"]
    out["prazo_dias"] = pd.to_numeric(df["prazo_dias"], errors="coerce").astype("Int64")

    # colunas ainda não identificadas do export -> jsonb, para não perder dado nenhum
    extras_df = df[colunas_extra].rename(columns=lambda c: c.lstrip("_"))
    out["colunas_nao_identificadas"] = extras_df.apply(
        lambda row: json.dumps(
            {k: (None if pd.isna(v) else float(v)) for k, v in row.items()}, default=str
        ),
        axis=1,
    )
    return out[COLS_TABELA]


def importar_arquivo(session, arquivo, tipo_operacao, competencia_id):
    """`arquivo` pode ser um caminho (str/Path) ou um buffer tipo st.file_uploader."""
    df = _preparar_dataframe(arquivo, tipo_operacao, competencia_id)
    df.to_sql(
        "notas_fiscais_itens", session.bind, if_exists="append", index=False,
        method="multi", chunksize=500,
    )
    return len(df)


def importar(session, empresa_cnpj, ano, mes, arquivo_entrada=None, arquivo_saida=None, substituir=False):
    """Fluxo completo: cria/acha a competência, checa duplicação, importa o(s) arquivo(s), marca status."""
    if not arquivo_entrada and not arquivo_saida:
        raise ValueError("Informe pelo menos um arquivo (Entrada e/ou Saída).")

    competencia_id = get_or_create_competencia(session, empresa_cnpj, ano, mes)
    removidos = checar_duplicacao(session, competencia_id, substituir)

    partes = []
    if removidos:
        partes.append(f"{removidos} itens antigos removidos (substituição).")
    if arquivo_entrada:
        n = importar_arquivo(session, arquivo_entrada, "entrada", competencia_id)
        partes.append(f"Entrada: {n} itens importados.")
    if arquivo_saida:
        n = importar_arquivo(session, arquivo_saida, "saida", competencia_id)
        partes.append(f"Saída: {n} itens importados.")

    session.execute(text("update competencias set status = 'importada' where id = :cid"), {"cid": competencia_id})
    session.commit()
    partes.append(f"Competência {competencia_id} pronta para cálculo.")
    return " ".join(partes)
