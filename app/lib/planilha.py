"""
Leitura/edição em grade (estilo planilha) dos itens de Entrada/Saída, e edição em lote dos valores de
referência das Rotinas 1024/1025 — usado pela página "ICMS Normal".

Por que existe este módulo: o analista está acostumado a mexer direto na planilha (Entrada/Saída) quando
alguma linha está errada, e a digitar os totais da Rotina 1024/1025 de uma vez, não CFOP por CFOP num
formulário. As funções aqui trocam consultas linha-a-linha por operações em lote (bater com o jeito de
trabalhar do usuário e também evitar o problema de performance encontrado em 05/08/2026).
"""
import pandas as pd
from sqlalchemy import text

COLUNAS_EDITAVEIS_ENTRADA = [
    "id", "nf_numero", "parceiro", "produto", "ncm", "cfop", "valor_produto",
    "aliq_icms", "base_icms", "valor_icms", "uf",
]
COLUNAS_EDITAVEIS_SAIDA = COLUNAS_EDITAVEIS_ENTRADA  # mesmo conjunto de colunas visíveis para as duas abas


def carregar_itens(session, competencia_id, tipo_operacao, cfop_filtro=None, busca=None, limite=500):
    """Devolve (DataFrame, total_sem_filtro_de_limite) — o total serve para avisar o usuário quando a
    tela está mostrando só uma parte dos itens."""
    where = ["competencia_id = :cid", "tipo_operacao = :tipo"]
    params = {"cid": competencia_id, "tipo": tipo_operacao}
    if cfop_filtro:
        where.append("cfop = :cfop")
        params["cfop"] = cfop_filtro
    if busca:
        where.append("(nf_numero ilike :busca or produto ilike :busca or parceiro ilike :busca)")
        params["busca"] = f"%{busca}%"
    where_sql = " and ".join(where)

    total = session.execute(
        text(f"select count(*) from notas_fiscais_itens where {where_sql}"), params
    ).scalar()

    params["limite"] = limite
    rows = session.execute(text(f"""
        select id, nf_numero, parceiro, produto, ncm, cfop, valor_produto, aliq_icms, base_icms,
               valor_icms, uf
        from notas_fiscais_itens
        where {where_sql}
        order by nf_numero
        limit :limite
    """), params).mappings().all()

    df = pd.DataFrame(rows, columns=COLUNAS_EDITAVEIS_ENTRADA)
    return df, total


def salvar_itens_editados(session, df_original, df_editado):
    """Compara linha a linha (pela coluna id) e só grava no banco o que realmente mudou. Retorna quantas
    linhas foram atualizadas."""
    if df_original.empty:
        return 0
    orig = df_original.set_index("id")
    edit = df_editado.set_index("id")
    campos = [c for c in COLUNAS_EDITAVEIS_ENTRADA if c != "id"]

    atualizados = 0
    for item_id in orig.index:
        if item_id not in edit.index:
            continue  # linha apagada na grade — não propaga exclusão aqui, por segurança
        mudou = False
        valores = {}
        for campo in campos:
            v_orig, v_edit = orig.loc[item_id, campo], edit.loc[item_id, campo]
            if pd.isna(v_orig) and pd.isna(v_edit):
                continue
            if v_orig != v_edit:
                mudou = True
            valores[campo] = None if pd.isna(v_edit) else v_edit
        if mudou:
            session.execute(text("""
                update notas_fiscais_itens
                set nf_numero=:nf_numero, parceiro=:parceiro, produto=:produto, ncm=:ncm, cfop=:cfop,
                    valor_produto=:valor_produto, aliq_icms=:aliq_icms, base_icms=:base_icms,
                    valor_icms=:valor_icms, uf=:uf
                where id=:id
            """), {**valores, "id": item_id})
            atualizados += 1
    if atualizados:
        session.commit()
    return atualizados


def resumo_por_cfop(session, competencia_id, tipo_operacao):
    rows = session.execute(text("""
        select ni.cfop, c.descricao, count(*) as n, sum(ni.base_icms) as base, sum(ni.valor_icms) as icms
        from notas_fiscais_itens ni
        join cfop c on c.codigo = ni.cfop
        where ni.competencia_id = :cid and ni.tipo_operacao = :tipo
        group by ni.cfop, c.descricao
        order by ni.cfop
    """), {"cid": competencia_id, "tipo": tipo_operacao}).mappings().all()
    return pd.DataFrame(rows, columns=["cfop", "descricao", "n", "base", "icms"])


def carregar_checkpoint_1024_editavel(session, competencia_id):
    """Uma linha por CFOP (de Entrada+Saída), com o calculado ao lado do valor de referência já salvo
    (se houver) — para editar tudo de uma vez numa grade, em vez de formulário CFOP por CFOP."""
    rows = session.execute(text("""
        with calc as (
            select cfop, sum(base_icms) as base_calc, sum(valor_icms) as icms_calc
            from notas_fiscais_itens where competencia_id = :cid group by cfop
        )
        select c.cfop, cf.descricao, c.base_calc, c.icms_calc,
               r.valor_base as base_1024, r.valor_icms as icms_1024
        from calc c
        join cfop cf on cf.codigo = c.cfop
        left join checkpoints_referencia r
            on r.competencia_id = :cid and r.fonte = 'rotina_1024' and r.cfop = c.cfop
        order by c.cfop
    """), {"cid": competencia_id}).mappings().all()
    return pd.DataFrame(rows, columns=["cfop", "descricao", "base_calc", "icms_calc", "base_1024", "icms_1024"])


def salvar_checkpoint_1024_bulk(session, competencia_id, df):
    """`df` é a grade editada (colunas cfop, base_1024, icms_1024) — grava só as linhas preenchidas."""
    salvos = 0
    for _, row in df.iterrows():
        if pd.isna(row.get("base_1024")) and pd.isna(row.get("icms_1024")):
            continue
        session.execute(text("delete from checkpoints_referencia "
                              "where competencia_id=:cid and fonte='rotina_1024' and cfop=:cfop"),
                         {"cid": competencia_id, "cfop": int(row["cfop"])})
        session.execute(text("""
            insert into checkpoints_referencia (competencia_id, fonte, cfop, valor_base, valor_icms)
            values (:cid, 'rotina_1024', :cfop, :base, :icms)
        """), {
            "cid": competencia_id, "cfop": int(row["cfop"]),
            "base": None if pd.isna(row.get("base_1024")) else float(row["base_1024"]),
            "icms": None if pd.isna(row.get("icms_1024")) else float(row["icms_1024"]),
        })
        salvos += 1
    session.commit()
    return salvos


def carregar_checkpoint_1025_editavel(session, competencia_id):
    rows = session.execute(text("""
        select a.linha, a.descricao, a.valor as valor_calc, r.valor_icms as valor_1025
        from apuracao_linhas a
        left join checkpoints_referencia r
            on r.competencia_id = a.competencia_id and r.fonte = 'rotina_1025' and r.linha = a.linha
        where a.competencia_id = :cid
        order by a.linha
    """), {"cid": competencia_id}).mappings().all()
    return pd.DataFrame(rows, columns=["linha", "descricao", "valor_calc", "valor_1025"])


def salvar_checkpoint_1025_bulk(session, competencia_id, df):
    salvos = 0
    for _, row in df.iterrows():
        if pd.isna(row.get("valor_1025")):
            continue
        session.execute(text("delete from checkpoints_referencia "
                              "where competencia_id=:cid and fonte='rotina_1025' and linha=:linha"),
                         {"cid": competencia_id, "linha": row["linha"]})
        session.execute(text("""
            insert into checkpoints_referencia (competencia_id, fonte, linha, valor_icms)
            values (:cid, 'rotina_1025', :linha, :valor)
        """), {"cid": competencia_id, "linha": row["linha"], "valor": float(row["valor_1025"])})
        salvos += 1
    session.commit()
    return salvos
