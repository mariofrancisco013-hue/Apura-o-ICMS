"""
Lógica de importação dos relatórios de Entrada/Saída — compartilhada entre scripts/import_relatorios.py
(linha de comando) e a página Streamlit "Importar Relatórios" (upload pelo navegador).

Mapeamento de colunas confirmado em 05/08/2026 (ver claude/metodologia-icms-normal.md no projeto) — os
exports vêm com cabeçalhos genéricos "Coluna1", "Coluna2"... por isso o mapeamento é posicional.
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


def importar_arquivo(session, arquivo, tipo_operacao, competencia_id):
    """`arquivo` pode ser um caminho (str/Path) ou um buffer tipo st.file_uploader."""
    cols = COLS_ENTRADA if tipo_operacao == "entrada" else COLS_SAIDA
    df = pd.read_excel(arquivo, sheet_name="Report", header=0, engine="xlrd")
    if len(df.columns) != len(cols):
        raise ValueError(
            f"Arquivo de {tipo_operacao} tem {len(df.columns)} colunas, esperado {len(cols)}. "
            f"O layout do export pode ter mudado — confira antes de importar."
        )
    df.columns = cols

    inseridos = 0
    for _, row in df.iterrows():
        extras = {c.lstrip("_"): (None if pd.isna(row[c]) else float(row[c]))
                  for c in cols if c.startswith("_")}
        session.execute(text("""
            insert into notas_fiscais_itens (
                competencia_id, tipo_operacao, parceiro, nf_numero, tipo_genero_item,
                data_emissao, data_entrada, produto, ncm, cfop, valor_produto,
                aliq_fcp, valor_fcp, aliq_icms, base_icms, valor_icms, valor_total,
                uf, prazo_dias, colunas_nao_identificadas
            ) values (
                :cid, :tipo, :parceiro, :nf, :tipo_genero,
                :dt_emissao, :dt_entrada, :produto, :ncm, :cfop, :valor_produto,
                :aliq_fcp, :valor_fcp, :aliq_icms, :base_icms, :valor_icms, :valor_total,
                :uf, :prazo, :extras
            )
        """), {
            "cid": competencia_id, "tipo": tipo_operacao,
            "parceiro": row.get("parceiro"), "nf": str(row.get("nf_numero")),
            "tipo_genero": str(row.get("tipo_genero_item")),
            "dt_emissao": row.get("data_emissao"),
            "dt_entrada": row.get("data_entrada") if tipo_operacao == "entrada" else None,
            "produto": row.get("produto"), "ncm": str(row.get("ncm")),
            "cfop": int(row["cfop"]), "valor_produto": float(row.get("valor_produto") or 0),
            "aliq_fcp": float(row.get("aliq_fcp") or 0) if "aliq_fcp" in cols else None,
            "valor_fcp": float(row.get("valor_fcp") or 0) if "valor_fcp" in cols else None,
            "aliq_icms": float(row.get("aliq_icms") or 0),
            "base_icms": float(row.get("base_icms") or 0),
            "valor_icms": float(row.get("valor_icms") or 0),
            "valor_total": float(row.get("valor_total") or 0),
            "uf": row.get("uf"), "prazo": int(row["prazo_dias"]) if pd.notna(row.get("prazo_dias")) else None,
            "extras": json.dumps(extras, default=str),
        })
        inseridos += 1
    session.commit()
    return inseridos


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
