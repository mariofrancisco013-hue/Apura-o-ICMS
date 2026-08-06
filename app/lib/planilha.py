"""
Leitura/edição em grade (estilo planilha) dos itens de Entrada/Saída, e edição em lote dos valores de
referência das Rotinas 1024/1025 — usado pela página "ICMS Normal".

Por que existe este módulo: o analista está acostumado a mexer direto na planilha (Entrada/Saída) quando
alguma linha está errada, e a digitar os totais da Rotina 1024/1025 de uma vez, não CFOP por CFOP num
formulário. As funções aqui trocam consultas linha-a-linha por operações em lote (bater com o jeito de
trabalhar do usuário e também evitar o problema de performance encontrado em 05/08/2026).
"""
import numpy as np
import pandas as pd
from sqlalchemy import text

COLUNAS_EDITAVEIS_ENTRADA = [
    "id", "nf_numero", "parceiro", "produto_codigo", "produto_descricao", "ncm", "cfop", "valor_produto",
    "aliq_icms", "base_icms", "valor_icms", "uf",
]
COLUNAS_EDITAVEIS_SAIDA = COLUNAS_EDITAVEIS_ENTRADA  # mesmo conjunto de colunas visíveis para as duas abas

# Colunas só de leitura, calculadas via join. NÃO fazem parte de COLUNAS_EDITAVEIS_* porque não são colunas
# reais de notas_fiscais_itens (salvar_itens_editados não deve tentar gravar nelas).
COLUNAS_TODAS = COLUNAS_EDITAVEIS_ENTRADA + ["ncm_descricao", "inconsistencia"]

# Rótulo curto por tipo de inconsistência, pra mostrar direto na grade (pedido do usuário em 06/08/2026:
# "as inconsistencias encontradas não estão sendo apresentadas na planilha de entrada e saida") — a
# descrição completa de cada uma continua só na aba Inconsistências, aqui é só um sinal de alerta. Público
# (sem "_" na frente) porque a página importa para montar o multiselect de filtro por tipo.
LABELS_INCONSISTENCIA = {
    "ncm_st_inconsistente": "NCM×ST divergente Entrada/Saída",
    "transferencia_nao_vinculada": "Transferência não vinculada",
    "ncm_tributado_como_st": "NCM tributado veio como ST",
    "ncm_tributado_novo": "NCM tributado novo (não cadastrado)",
}


def _formatar_inconsistencia(tipos_raw):
    """`tipos_raw` vem de um string_agg(distinct tipo, ',') do SQL — NULL quando o item não tem
    inconsistência pendente.

    Achado em 06/08/2026: usar `if not tipos_raw` pra detectar "vazio" quebrava em produção — o pandas
    (3.0) converte o NULL/None dessa coluna pra NaN (float) ao montar o DataFrame, e `NaN` é "verdadeiro"
    em Python (bool(float('nan')) é True), então o `if not` deixava passar e o `.split()` de um float
    estourava AttributeError. `pd.isna()` reconhece None, NaN e pd.NA igual, então é a checagem certa
    aqui."""
    if pd.isna(tipos_raw):
        return None
    labels = [LABELS_INCONSISTENCIA.get(t, t) for t in str(tipos_raw).split(",")]
    return "⚠️ " + "; ".join(labels)


def carregar_itens(session, competencia_id, tipo_operacao, empresa_id, cfop_filtro=None, busca=None,
                    limite=500, tipos_inconsistencia=None, ncm_filtro=None):
    """Devolve (DataFrame, total_sem_filtro_de_limite) — o total serve para avisar o usuário quando a
    tela está mostrando só uma parte dos itens.

    `ncm_descricao` só vem preenchida para os NCMs que estão cadastrados como "tributados" para esta
    empresa (aba NCMs Tributados) — pedido do usuário em 06/08/2026 ("traga a descrição dos NCMs
    tributados somente deles"). Para os demais NCMs a coluna fica em branco; a tabela de referência oficial
    completa (10.515 códigos, `ncm`) continua no banco, mas só é usada aqui através do cadastro de
    tributados, não para todo NCM que aparecer na nota.

    `inconsistencia` mostra um resumo (rótulo, não a descrição completa) de toda inconsistência PENDENTE
    ligada a este item via `inconsistencia_itens` — pedido do usuário em 06/08/2026, antes só dava pra ver
    na aba Inconsistências, separada da planilha. `tipos_inconsistencia` (lista de códigos de tipo, ex:
    ["ncm_tributado_novo"]) filtra a grade pra mostrar só os itens com inconsistência PENDENTE de um dos
    tipos escolhidos — pedido em 06/08/2026 ("preciso filtrar o erro que está sendo apresentado na aba
    inconsistência"). Lista vazia/None = sem filtro, mostra todos os itens.

    `ncm_filtro` filtra por prefixo do NCM (ex: "8213" pega "82130000", "82131000"...) — pedido em
    06/08/2026 ("filtrar por ncm também ajuda"), útil pra achar rápido todos os itens de um NCM/capítulo
    específico, inclusive combinando com o filtro de tipo de inconsistência acima."""
    where = ["ni.competencia_id = :cid", "ni.tipo_operacao = :tipo"]
    params = {"cid": competencia_id, "tipo": tipo_operacao, "empresa_id": empresa_id}
    if cfop_filtro:
        where.append("ni.cfop = :cfop")
        params["cfop"] = cfop_filtro
    if ncm_filtro:
        where.append("ni.ncm ilike :ncm_filtro")
        params["ncm_filtro"] = f"{ncm_filtro.strip()}%"
    if busca:
        where.append("(ni.nf_numero ilike :busca or ni.produto_codigo ilike :busca or "
                      "ni.produto_descricao ilike :busca or ni.parceiro ilike :busca)")
        params["busca"] = f"%{busca}%"
    if tipos_inconsistencia:
        where.append("""exists (
            select 1 from inconsistencia_itens ii2
            join inconsistencias i2 on i2.id = ii2.inconsistencia_id and i2.status = 'pendente'
            where ii2.nf_item_id = ni.id and i2.tipo = any(:tipos_inc)
        )""")
        params["tipos_inc"] = list(tipos_inconsistencia)
    where_sql = " and ".join(where)

    total = session.execute(
        text(f"select count(*) from notas_fiscais_itens ni where {where_sql}"), params
    ).scalar()

    params["limite"] = limite
    rows = session.execute(text(f"""
        select ni.id, ni.nf_numero, ni.parceiro, ni.produto_codigo, ni.produto_descricao, ni.ncm, ni.cfop,
               ni.valor_produto, ni.aliq_icms, ni.base_icms, ni.valor_icms, ni.uf,
               n.descricao as ncm_descricao, inc.tipos_pendentes
        from notas_fiscais_itens ni
        left join ncms_tributados t on t.ncm = ni.ncm and t.empresa_id = :empresa_id
        left join ncm n on n.codigo = t.ncm
        left join lateral (
            select string_agg(distinct i.tipo, ',') as tipos_pendentes
            from inconsistencia_itens ii
            join inconsistencias i on i.id = ii.inconsistencia_id and i.status = 'pendente'
            where ii.nf_item_id = ni.id
        ) inc on true
        where {where_sql}
        order by ni.nf_numero
        limit :limite
    """), params).mappings().all()

    df = pd.DataFrame(rows, columns=COLUNAS_EDITAVEIS_ENTRADA + ["ncm_descricao", "tipos_pendentes"])
    df["inconsistencia"] = df["tipos_pendentes"].apply(_formatar_inconsistencia) if not df.empty else []
    return df[COLUNAS_TODAS], total


def _para_tipo_nativo(v):
    """Converte escalares numpy (numpy.int64, numpy.float64, numpy.bool_...) para o tipo nativo do Python
    equivalente.

    Achado em 06/08/2026 investigando `sqlalchemy.exc.ProgrammingError` ao salvar uma edição de CFOP na
    planilha: `pd.DataFrame(rows, ...)` (usado em carregar_itens) e o DataFrame que o `st.data_editor`
    devolve guardam colunas numéricas como numpy.int64/float64, não int/float nativos do Python — mesmo
    quando o valor de origem já era um int nativo do psycopg2. O psycopg2 não sabe adaptar tipos numpy
    diretamente como parâmetro de bind (`can't adapt type 'numpy.int64'`), e isso derruba a query inteira
    com ProgrammingError assim que qualquer campo numérico (cfop, valor_produto, aliq_icms, base_icms,
    valor_icms, ou o próprio id) muda de valor — não é sobre produto_codigo/produto_descricao existirem ou
    não na tabela."""
    if isinstance(v, np.bool_):
        return bool(v)
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, np.floating):
        return float(v)
    return v


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
            valores[campo] = None if pd.isna(v_edit) else _para_tipo_nativo(v_edit)
        if mudou:
            session.execute(text("""
                update notas_fiscais_itens
                set nf_numero=:nf_numero, parceiro=:parceiro, produto_codigo=:produto_codigo,
                    produto_descricao=:produto_descricao, ncm=:ncm, cfop=:cfop,
                    valor_produto=:valor_produto, aliq_icms=:aliq_icms, base_icms=:base_icms,
                    valor_icms=:valor_icms, uf=:uf
                where id=:id
            """), {**valores, "id": _para_tipo_nativo(item_id)})
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


def carregar_totalizador(session, competencia_id, tipo_operacao, cfop_filtro=None, ncm_filtro=None):
    """Visão SINTÉTICA da planilha — pedido do usuário em 06/08/2026: poder analisar Entrada/Saída
    totalizado por UF + Código do Produto + Alíquota de ICMS, em vez de item a item (visão ANALÍTICA, que
    continua disponível — o analista escolhe qual das duas quer ver, não substitui uma pela outra).

    Junta produto_descricao pelo `min()` (assume que o mesmo código de produto tem sempre a mesma
    descrição — é o caso normal; se não for, ainda soma certo, só a descrição mostrada na linha totalizada
    pode não refletir 100% das notas por trás). `ncm_filtro` filtra por prefixo do NCM, mesmo critério da
    visão Analítica (ver carregar_itens)."""
    where = ["ni.competencia_id = :cid", "ni.tipo_operacao = :tipo"]
    params = {"cid": competencia_id, "tipo": tipo_operacao}
    if cfop_filtro:
        where.append("ni.cfop = :cfop")
        params["cfop"] = cfop_filtro
    if ncm_filtro:
        where.append("ni.ncm ilike :ncm_filtro")
        params["ncm_filtro"] = f"{ncm_filtro.strip()}%"
    where_sql = " and ".join(where)

    rows = session.execute(text(f"""
        select ni.uf, ni.produto_codigo, min(ni.produto_descricao) as produto_descricao, ni.aliq_icms,
               count(*) as n_itens, sum(ni.valor_produto) as valor_produto,
               sum(ni.base_icms) as base_icms, sum(ni.valor_icms) as valor_icms
        from notas_fiscais_itens ni
        where {where_sql}
        group by ni.uf, ni.produto_codigo, ni.aliq_icms
        order by ni.uf, ni.produto_codigo, ni.aliq_icms
    """), params).mappings().all()
    return pd.DataFrame(rows, columns=[
        "uf", "produto_codigo", "produto_descricao", "aliq_icms", "n_itens",
        "valor_produto", "base_icms", "valor_icms",
    ])


# Faixa de CFOP por direção (padrão nacional): 1xxx/2xxx/3xxx = Entrada, 5xxx/6xxx/7xxx = Saída. Usado só
# para decidir em qual aba (Entrada/Saída) cada CFOP da Rotina 1024 aparece — não depende de o CFOP ter
# sido efetivamente importado num relatório de NF (ver nota abaixo).
_FAIXA_CFOP = {"entrada": (1, 2, 3), "saida": (5, 6, 7)}


def carregar_checkpoint_1024_editavel(session, competencia_id, tipo_operacao):
    """Uma linha por CFOP, com o calculado (a partir dos itens importados) ao lado do valor de referência
    da Rotina 1024 (se houver) — para editar tudo de uma vez numa grade, em vez de formulário CFOP por
    CFOP.

    Traz TODO CFOP que aparecer OU no calculado OU na Rotina 1024 (união, não interseção) — achado em
    06/08/2026: alguns CFOPs da Rotina 1024 (ex: 1353, 1407, 1933, 2353 na Sodine) não vêm em NENHUM
    relatório de Entrada/Saída importado, porque são lançados direto no sistema contábil (mesmo caso já
    conhecido do CFOP 1602). A versão anterior só mostrava CFOPs que já existiam no calculado, então esses
    ficavam invisíveis na conferência mesmo aparecendo na Rotina 1024 (com valor zerado, nesses casos —
    mas o usuário não tinha como confirmar isso olhando só a grade)."""
    faixa = _FAIXA_CFOP[tipo_operacao]
    rows = session.execute(text("""
        with calc as (
            select cfop, sum(base_icms) as base_calc, sum(valor_icms) as icms_calc
            from notas_fiscais_itens where competencia_id = :cid group by cfop
        ),
        ref as (
            select cfop, valor_base, valor_icms
            from checkpoints_referencia where competencia_id = :cid and fonte = 'rotina_1024'
        ),
        todos_cfops as (
            select cfop from calc
            union
            select cfop from ref
        )
        select t.cfop, cf.descricao,
               coalesce(c.base_calc, 0) as base_calc, coalesce(c.icms_calc, 0) as icms_calc,
               r.valor_base as base_1024, r.valor_icms as icms_1024
        from todos_cfops t
        left join calc c on c.cfop = t.cfop
        left join ref r on r.cfop = t.cfop
        left join cfop cf on cf.codigo = t.cfop
        order by t.cfop
    """), {"cid": competencia_id}).mappings().all()
    df = pd.DataFrame(rows, columns=["cfop", "descricao", "base_calc", "icms_calc", "base_1024", "icms_1024"])
    if df.empty:
        return df
    df["descricao"] = df["descricao"].fillna("(CFOP não cadastrado na tabela de referência)")
    return df[df["cfop"].apply(lambda c: (c // 1000) in faixa)].reset_index(drop=True)


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
