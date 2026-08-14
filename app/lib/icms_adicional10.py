"""
Adicional 10% — pedido do usuário em 13/08/2026: "AGORA AO LADO DA SUBSTITUIÇÃO CRIE UMA COM O NOME:
ADICIONAL 10% UTILIZANDO A LOGICA DA PLANILHA ANEXA" (arquivo real: "ADICIONAL 10  ATACADO F3.xls").

Lógica reconstruída direto do XML da planilha real do usuário (não por tentativa — mesma técnica já usada
pra reverse-engenheirar a planilha de Crédito Presumido, ver app/lib/icms_st.py). A planilha tem três abas:

- **FILTRO** — cadastro de clientes (código -> "Sim"/"Exceção" -> se as NFs desse cliente contam ou não na
  base do Adicional 10%). Cadastro GLOBAL do usuário (2100+ clientes na amostra real), não muda por mês.
- **NFES** — lista de Notas Fiscais emitidas (um acumulado de vários meses na planilha do usuário: N° Trans,
  NFE, Série, T.V, Filial, Emissão, CNPJ, RCA, Cód. Cliente, Cliente, UF, IE, Valor Total, OBS — mais duas
  colunas CALCULADAS pela própria planilha, "CALCULA" (VLOOKUP no FILTRO) e "COMPETENCIA", que este módulo
  NÃO importa como estão — ver "Por que recalcular, não importar as colunas calculadas" abaixo.
- **RESUMO** — uma linha por mês, com as fórmulas finais (extraídas direto do XML):
    C (10% FATURAMENTO)   = B (FATURAMENTO) × 10%
    D (VENDAS)            = SUMIFS(NFES!VL_TOTAL, NFES!CALCULA, "Sim", NFES!COMPETENCIA, <mês>)
    E (BASE DE CALCULO)   = SE((D − C) < 0, 0, (D − C))
    F (ADICIONAL ICMS 1%) = (E × 19,31%) × 1%
    G (ADICIONAL ICMS 4%) = (E × 80,69%) × 4%
  FATURAMENTO (coluna B) NÃO tem fórmula — é digitado à mão pelo analista, mês a mês, na planilha original
  (confirmado inspecionando o XML: célula sem <f>, só <v>). Por isso vira um campo editável na tela (ver
  `carregar_faturamento`/`salvar_faturamento`), não algo importado de um relatório.

O split 19,31% / 80,69% é uma constante fixa da planilha do usuário (não vem de nenhuma tabela/fórmula —
está "hardcoded" nas células F/G de todas as linhas da amostra) — replicado aqui como constante
(`PCT_BASE_ADICIONAL_1`/`PCT_BASE_ADICIONAL_4`). Se o usuário precisar ajustar essa proporção no futuro
(ex: mudança na composição de produtos), é só atualizar essas duas constantes.

### Por que recalcular, não importar as colunas calculadas

`calcular_adicional10` recalcula VENDAS do zero (junção NFs × cadastro de clientes), em vez de confiar nas
colunas "CALCULA"/"COMPETENCIA" já calculadas dentro do arquivo da planilha — mesmo princípio já usado no
resto da plataforma (ex: Crédito Presumido recalcula em vez de usar os valores já computados da planilha
"CALCULO SUBV"). Dois motivos concretos, achados validando contra os dados reais:

1. **COMPETENCIA (coluna da planilha) está ausente em ~37% das linhas reais** — não dá pra confiar nela pra
   agrupar por mês. Este módulo deriva a competência da data de **Emissão** de cada NF (ano/mês), que está
   presente em 100% das linhas com dado real.
2. **CALCULA (coluna da planilha) é o resultado cacheado de uma fórmula VLOOKUP** — reflete o cadastro
   FILTRO no momento em que a fórmula rodou, não necessariamente o cadastro atual. Validado contra os 6
   meses reais da planilha (Jan–Jun/2026): recalculando via junção com o cadastro de clientes (mantendo a
   primeira ocorrência de cada código, igual o comportamento de um VLOOKUP), 5 dos 6 meses bateram exato
   com "VENDAS" da planilha; o 6º (Março/2026) teve uma pequena diferença (R$ 11.875,17 em cima de uma base
   de R$ 176 mil) que não chega a afetar o resultado final desse mês (BASE DE CALCULO ficou zerada nos dois
   casos) — rastreada até códigos de cliente DUPLICADOS no cadastro FILTRO do usuário com classificação
   CONFLITANTE (ex: código 630 aparece como "Sim" numa linha e "Exceção" noutra) — um problema de qualidade
   de dado no cadastro original do usuário, não um bug deste módulo. `listar_clientes_conflitantes` expõe
   esses casos na tela pra o analista revisar/corrigir o cadastro.
"""
import pandas as pd
from sqlalchemy import text

# Constantes extraídas direto das fórmulas da planilha real (ver docstring do módulo).
PCT_FATURAMENTO_LIMITE = 10.0       # C = FATURAMENTO x 10%
PCT_BASE_ADICIONAL_1 = 19.31        # fatia da BASE sujeita ao adicional de 1%
PCT_BASE_ADICIONAL_4 = 80.69        # fatia da BASE sujeita ao adicional de 4%
ALIQ_ADICIONAL_1 = 1.0
ALIQ_ADICIONAL_4 = 4.0

MESES_PT = {
    1: "Jan", 2: "Fev", 3: "Mar", 4: "Abr", 5: "Mai", 6: "Jun",
    7: "Jul", 8: "Ago", 9: "Set", 10: "Out", 11: "Nov", 12: "Dez",
}

COLS_NFES_RAW = [
    "n_trans", "nfe", "serie", "tv", "filial", "emissao", "cnpj", "rca", "cod_cliente", "cliente_nome",
    "uf", "ie", "calcula_raw", "competencia_raw", "vl_total", "obs",
]

COLS_NFES_TABELA = [
    "n_trans", "nfe", "serie", "tv", "filial", "emissao", "cnpj", "rca", "cod_cliente", "cliente_nome",
    "uf", "ie", "vl_total", "obs",
]

# Colunas posicionais do export BRUTO do Winthor (achado em 13/08/2026, arquivo real do usuário
# "10 f3.xlsx" — sheet "Report", sem cabeçalho, 22 colunas). Layout diferente da aba "NFES" da planilha
# consolidada do usuário (que já vem com cabeçalho e só 16 colunas, incluindo duas colunas CALCULADAS —
# "Calcula"/"Competencia" — que não existem aqui): este é provavelmente o export original que o usuário
# cola na aba NFES da planilha consolidada antes de montar as fórmulas. As 12 primeiras colunas batem
# exatas com o início da aba NFES (N Trans .. IE); dali em diante o layout diverge — colunas 12/13/15/16/
# 17/18/19 sempre vazias ou constantes na amostra real (baixo valor informativo, não gravadas), coluna 14 é
# o Valor Total (confirmado pela faixa de valores — bate com o padrão de VL_TOTAL da aba NFES), coluna 20 é
# um código de forma de pagamento (D/PIX/PBC1-4/PBD1-2 na amostra — não usado no cálculo do Adicional 10%,
# não gravado) e coluna 21 é OBS (texto livre, ex: "C - DESISTIU DA COMPRA BALCAO").
COLS_RELATORIO10_RAW = [
    "n_trans", "nfe", "serie", "tv", "filial", "emissao", "cnpj", "rca", "cod_cliente", "cliente_nome",
    "uf", "ie", "_col12", "_col13", "vl_total", "_col15", "_col16", "_col17", "_col18", "_col19",
    "forma_pagamento", "obs",
]


def _texto_ou_none(valor):
    if valor is None:
        return None
    if isinstance(valor, float) and pd.isna(valor):
        return None
    texto = str(valor).strip()
    return texto or None


def _cod_cliente_ou_none(valor):
    """COD.C vem como float do Excel (ex: 1.0) — normaliza pra int, tratando NaN."""
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return None
    try:
        return int(float(valor))
    except (TypeError, ValueError):
        return None


# ==============================================================================================
# Cadastro de clientes (aba "FILTRO" da planilha) — cadastro GLOBAL, mesmo princípio do
# cadastro_fornecedores_st do módulo ICMS ST.
# ==============================================================================================

def parse_filtro_clientes(arquivo_ou_planilha) -> pd.DataFrame:
    """Aceita tanto um arquivo com uma aba "FILTRO" (a planilha inteira do usuário) quanto um arquivo cuja
    primeira aba já seja o cadastro — procura a aba "FILTRO" pelo nome; se não achar, usa a primeira.
    Colunas esperadas (com cabeçalho): COD. C | CALCULA | CLIENTE."""
    # engine="calamine": mesmo motivo do resto do projeto (ver app/lib/importacao.py) — os exports do
    # Winthor têm um XML fora do padrão que quebra o openpyxl (achado em produção em 13/08/2026: TypeError
    # "BookView.__init__() got an unexpected keyword argument 'WindowWidth'" ao importar um arquivo .xlsx
    # real do usuário — o mesmo problema já visto antes com outros arquivos .xls/.xlsx do Winthor).
    xl = pd.ExcelFile(arquivo_ou_planilha, engine="calamine")
    sheet = "FILTRO" if "FILTRO" in xl.sheet_names else xl.sheet_names[0]
    df = xl.parse(sheet)
    if df.shape[1] < 3:
        raise ValueError(
            f"A aba \"{sheet}\" tem {df.shape[1]} coluna(s) — esperado pelo menos 3 (Código do Cliente, "
            f"Calcula, Cliente). Confira se é mesmo a aba \"FILTRO\" do cadastro de clientes."
        )
    df = df.iloc[:, :3].copy()
    df.columns = ["cod_cliente", "calcula", "cliente_nome"]
    df["cod_cliente"] = df["cod_cliente"].apply(_cod_cliente_ou_none)
    df = df[df["cod_cliente"].notna()].copy()
    df["calcula"] = df["calcula"].apply(_texto_ou_none)
    df["cliente_nome"] = df["cliente_nome"].apply(_texto_ou_none)
    return df[["cod_cliente", "calcula", "cliente_nome"]].reset_index(drop=True)


def salvar_clientes_filtro(session, df: pd.DataFrame, usuario_email: str = None) -> int:
    """Upsert por cod_cliente — nunca apaga cliente que não veio na importação atual (mesmo padrão do
    cadastro_fornecedores_st). Quando o mesmo código aparece mais de uma vez no arquivo (achado em dado
    real: 63 códigos duplicados, alguns com classificação conflitante), fica a PRIMEIRA ocorrência — mesmo
    comportamento de um VLOOKUP (que para no primeiro match)."""
    if df.empty:
        return 0
    dedup = df.drop_duplicates(subset="cod_cliente", keep="first")
    n = 0
    for _, row in dedup.iterrows():
        session.execute(text("""
            insert into icms_adicional10_clientes (cod_cliente, calcula, cliente_nome, atualizado_em)
            values (:cod, :calcula, :nome, now())
            on conflict (cod_cliente) do update set
                calcula = excluded.calcula, cliente_nome = excluded.cliente_nome, atualizado_em = now()
        """), {"cod": int(row["cod_cliente"]), "calcula": row["calcula"], "nome": row["cliente_nome"]})
        n += 1
    session.commit()
    return n


def carregar_clientes_filtro(session) -> pd.DataFrame:
    rows = session.execute(text(
        "select cod_cliente, calcula, cliente_nome from icms_adicional10_clientes order by cod_cliente"
    )).mappings().all()
    return pd.DataFrame(rows, columns=["cod_cliente", "calcula", "cliente_nome"])


def salvar_cadastro_clientes_editado(session, df_original: pd.DataFrame, df_editado: pd.DataFrame) -> dict:
    """Grava a grade editável (`st.data_editor` com `num_rows="dynamic"`) da aba "Cadastro de Clientes" —
    pedido do usuário em 13/08/2026: "não é melhor ter uma aba com os cadastros ... para de lá ir para o
    cálculo?" (até então o cadastro só dava pra ver/buscar, editar de verdade só reimportando a planilha
    inteira). `cod_cliente` é a própria chave primária da tabela (não tem `id` separado) — mesma lógica de
    diff já usada em `lib/lancamentos_manuais.py`/`lib/ncm_tributado.py`, só que chaveada por `cod_cliente`:
    linha removida na grade (código que sumiu) é excluída do cadastro; o resto (linha nova ou editada) é
    upsert via `salvar_clientes_filtro`. Devolve {"salvos": n, "removidos": n}."""
    cods_originais = set(df_original["cod_cliente"].dropna().astype(int)) if not df_original.empty else set()
    cods_editados = (
        set(df_editado["cod_cliente"].dropna().astype(int))
        if "cod_cliente" in df_editado.columns and not df_editado.empty else set()
    )
    removidos = cods_originais - cods_editados
    for cod in removidos:
        session.execute(
            text("delete from icms_adicional10_clientes where cod_cliente = :cod"), {"cod": int(cod)}
        )
    if removidos:
        session.commit()

    validas = df_editado[df_editado["cod_cliente"].notna()].copy() if "cod_cliente" in df_editado.columns \
        else df_editado.iloc[0:0]
    validas["calcula"] = validas["calcula"].where(validas["calcula"].notna(), None)
    validas["cliente_nome"] = validas["cliente_nome"].where(validas["cliente_nome"].notna(), None)
    salvos = salvar_clientes_filtro(session, validas) if not validas.empty else 0

    return {"salvos": salvos, "removidos": len(removidos)}


def listar_clientes_conflitantes(arquivo_ou_planilha) -> pd.DataFrame:
    """Auditoria do arquivo importado (não do cadastro já salvo): códigos de cliente que aparecem mais de
    uma vez na aba FILTRO com classificações DIFERENTES entre si (ex: "Sim" numa linha, "Exceção" noutra) —
    achado real no arquivo do usuário (63 códigos duplicados, alguns conflitantes). Como a importação fica
    só com a primeira ocorrência (ver salvar_clientes_filtro), esses casos merecem revisão manual do
    analista pra confirmar qual classificação é a certa."""
    df = parse_filtro_clientes(arquivo_ou_planilha)
    agrupado = df.groupby("cod_cliente")["calcula"].apply(lambda s: s.dropna().str.lower().nunique())
    codigos_conflitantes = agrupado[agrupado > 1].index
    if len(codigos_conflitantes) == 0:
        return pd.DataFrame(columns=["cod_cliente", "cliente_nome", "calcula"])
    return df[df["cod_cliente"].isin(codigos_conflitantes)].sort_values("cod_cliente").reset_index(drop=True)


# ==============================================================================================
# NFs (aba "NFES" da planilha) — importadas por competência (apagar+inserir), agrupadas pela data de
# Emissão de cada NF.
# ==============================================================================================

def parse_nfes(arquivo_ou_planilha) -> pd.DataFrame:
    """Aceita tanto um arquivo com uma aba "NFES" quanto um arquivo cuja primeira aba já seja a lista de
    NFs. Colunas esperadas (com cabeçalho, na ordem): N° Trans, NFE, Série, T.V, Filial, Emissão, CNPJ,
    RCA, Cód. Cliente, Cliente, UF, IE, Calcula, Competência, Valor Total, OBS — as duas colunas calculadas
    (Calcula/Competência) são ignoradas (ver docstring do módulo, "Por que recalcular"). Linhas sem data de
    Emissão são descartadas (linhas em branco/rodapé de totais — achado em dado real: a última linha da
    amostra era uma linha de total, sem Emissão)."""
    # engine="calamine": mesmo motivo do resto do projeto (ver app/lib/importacao.py) — os exports do
    # Winthor têm um XML fora do padrão que quebra o openpyxl (achado em produção em 13/08/2026: TypeError
    # "BookView.__init__() got an unexpected keyword argument 'WindowWidth'" ao importar um arquivo .xlsx
    # real do usuário — o mesmo problema já visto antes com outros arquivos .xls/.xlsx do Winthor).
    xl = pd.ExcelFile(arquivo_ou_planilha, engine="calamine")
    sheet = "NFES" if "NFES" in xl.sheet_names else xl.sheet_names[0]
    df = xl.parse(sheet)
    if df.shape[1] < 15:
        raise ValueError(
            f"A aba \"{sheet}\" tem {df.shape[1]} coluna(s) — esperado pelo menos 15 (N° Trans, NFE, "
            f"Série, T.V, Filial, Emissão, CNPJ, RCA, Cód. Cliente, Cliente, UF, IE, Calcula, "
            f"Competência, Valor Total). Confira se é mesmo a aba \"NFES\"."
        )
    df = df.iloc[:, :16].copy()
    df.columns = COLS_NFES_RAW[: df.shape[1]]

    out = pd.DataFrame()
    out["n_trans"] = df["n_trans"].apply(_texto_ou_none)
    out["nfe"] = df["nfe"].apply(_texto_ou_none)
    out["serie"] = df["serie"].apply(_texto_ou_none)
    out["tv"] = df["tv"].apply(_texto_ou_none)
    out["filial"] = df["filial"].apply(_texto_ou_none)
    out["emissao"] = pd.to_datetime(df["emissao"], errors="coerce").dt.date
    out["cnpj"] = df["cnpj"].apply(_texto_ou_none)
    out["rca"] = df["rca"].apply(_texto_ou_none)
    out["cod_cliente"] = df["cod_cliente"].apply(_cod_cliente_ou_none)
    out["cliente_nome"] = df["cliente_nome"].apply(_texto_ou_none)
    out["uf"] = df["uf"].apply(_texto_ou_none)
    out["ie"] = df["ie"].apply(_texto_ou_none)
    out["vl_total"] = pd.to_numeric(df["vl_total"], errors="coerce")
    out["obs"] = df["obs"].apply(_texto_ou_none) if "obs" in df.columns else None

    out = out[out["emissao"].notna()].reset_index(drop=True)
    return out[COLS_NFES_TABELA]


def parse_relatorio_10(arquivo) -> pd.DataFrame:
    """Lê o export BRUTO do Winthor (.xls/.xlsx, sheet "Report", sem cabeçalho, 22 colunas — achado em
    13/08/2026, arquivo real do usuário "10 f3.xlsx") — layout diferente da aba "NFES" da planilha
    consolidada (ver COLS_RELATORIO10_RAW). É provavelmente o export original que o usuário cola na aba
    NFES antes de montar as fórmulas da planilha consolidada — esta função permite importar esse export
    bruto DIRETO, sem precisar passar pela planilha consolidada inteira. Devolve no mesmo formato de
    `parse_nfes` (COLS_NFES_TABELA), pra poder ser gravado com `salvar_nfes_por_competencia` do jeito de
    sempre."""
    df = pd.read_excel(arquivo, sheet_name="Report", header=None, engine="calamine")
    if len(df.columns) != len(COLS_RELATORIO10_RAW):
        raise ValueError(
            f"Arquivo tem {len(df.columns)} coluna(s) — esperado {len(COLS_RELATORIO10_RAW)} (export "
            f"bruto do Winthor, sheet \"Report\"). Se for a planilha consolidada (com abas FILTRO/NFES/"
            f"RESUMO), envie ela inteira em vez de só esta aba."
        )
    df.columns = COLS_RELATORIO10_RAW

    out = pd.DataFrame()
    out["n_trans"] = df["n_trans"].apply(_texto_ou_none)
    out["nfe"] = df["nfe"].apply(_texto_ou_none)
    out["serie"] = df["serie"].apply(_texto_ou_none)
    out["tv"] = df["tv"].apply(_texto_ou_none)
    out["filial"] = df["filial"].apply(_texto_ou_none)
    out["emissao"] = pd.to_datetime(df["emissao"], errors="coerce").dt.date
    out["cnpj"] = df["cnpj"].apply(_texto_ou_none)
    out["rca"] = df["rca"].apply(_texto_ou_none)
    out["cod_cliente"] = df["cod_cliente"].apply(_cod_cliente_ou_none)
    out["cliente_nome"] = df["cliente_nome"].apply(_texto_ou_none)
    out["uf"] = df["uf"].apply(_texto_ou_none)
    out["ie"] = df["ie"].apply(_texto_ou_none)
    out["vl_total"] = pd.to_numeric(df["vl_total"], errors="coerce")
    out["obs"] = df["obs"].apply(_texto_ou_none)

    out = out[out["emissao"].notna()].reset_index(drop=True)
    return out[COLS_NFES_TABELA]


def salvar_nfes_por_competencia(session, empresa_cnpj: str, df: pd.DataFrame, get_or_create_competencia) -> dict:
    """Agrupa as NFs pela competência (ano/mês da Emissão de cada linha) e grava cada grupo na sua própria
    competência (apagar+inserir, criando a competência se ainda não existir) — pedido implícito do usuário
    ao anexar um arquivo que acumula vários meses de uma vez: um único upload alimenta todos os meses
    presentes no arquivo, sem precisar reimportar mês a mês. Devolve um dict {"MM/AAAA": quantidade} com o
    que foi importado, pra mostrar na tela."""
    resultado = {}
    if df.empty:
        return resultado
    df = df.copy()
    df["ano"] = pd.to_datetime(df["emissao"]).dt.year
    df["mes"] = pd.to_datetime(df["emissao"]).dt.month
    for (ano, mes), grupo in df.groupby(["ano", "mes"]):
        cid = get_or_create_competencia(session, empresa_cnpj, int(ano), int(mes), modulo="icms_adicional_10")
        session.execute(
            text("delete from icms_adicional10_nfes_itens where competencia_id = :cid"), {"cid": cid}
        )
        out = grupo[COLS_NFES_TABELA].copy()
        out.insert(0, "competencia_id", cid)
        out.to_sql(
            "icms_adicional10_nfes_itens", session.bind, if_exists="append", index=False, method="multi",
            chunksize=500,
        )
        resultado[f"{mes:02d}/{ano}"] = len(grupo)
    session.commit()
    return resultado


def carregar_nfes_itens(session, competencia_id: int) -> pd.DataFrame:
    rows = session.execute(text("""
        select n_trans, nfe, serie, tv, filial, emissao, cnpj, rca, cod_cliente, cliente_nome, uf, ie,
               vl_total, obs
        from icms_adicional10_nfes_itens where competencia_id = :cid order by emissao, nfe
    """), {"cid": competencia_id}).mappings().all()
    return pd.DataFrame(rows, columns=COLS_NFES_TABELA)


# ==============================================================================================
# Faturamento mensal (digitado manualmente, sem fórmula na planilha original) — guardado em
# checkpoints_referencia (fonte='manual_adicional_10'), mesmo padrão já usado pelos valores manuais da
# ICMS PE.
# ==============================================================================================

def carregar_faturamento(session, competencia_id: int):
    v = session.execute(text("""
        select valor_icms from checkpoints_referencia
        where competencia_id = :cid and fonte = 'manual_adicional_10' and linha = 'faturamento'
    """), {"cid": competencia_id}).scalar()
    return float(v) if v is not None else None


def salvar_faturamento(session, competencia_id: int, valor) -> None:
    session.execute(text("""
        delete from checkpoints_referencia
        where competencia_id = :cid and fonte = 'manual_adicional_10' and linha = 'faturamento'
    """), {"cid": competencia_id})
    session.execute(text("""
        insert into checkpoints_referencia (competencia_id, fonte, linha, valor_icms)
        values (:cid, 'manual_adicional_10', 'faturamento', :valor)
    """), {"cid": competencia_id, "valor": float(valor)})
    session.commit()


def parse_resumo_faturamento(arquivo_ou_planilha) -> dict:
    """Lê a aba "RESUMO" da planilha do usuário (se existir) e devolve um dict {(ano, mes): faturamento} —
    usado só pra PRÉ-PREENCHER o campo de Faturamento na tela quando o usuário importa a planilha inteira
    (o valor continua editável/sobrescrevível depois). Não falha se a aba não existir — devolve {}."""
    # engine="calamine": mesmo motivo do resto do projeto (ver app/lib/importacao.py) — os exports do
    # Winthor têm um XML fora do padrão que quebra o openpyxl (achado em produção em 13/08/2026: TypeError
    # "BookView.__init__() got an unexpected keyword argument 'WindowWidth'" ao importar um arquivo .xlsx
    # real do usuário — o mesmo problema já visto antes com outros arquivos .xls/.xlsx do Winthor).
    xl = pd.ExcelFile(arquivo_ou_planilha, engine="calamine")
    if "RESUMO" not in xl.sheet_names:
        return {}
    df = xl.parse("RESUMO", header=1)
    if df.shape[1] < 2 or "COMP." not in df.columns or "FATURAMENTO" not in df.columns:
        return {}
    mes_map = {v.lower(): k for k, v in MESES_PT.items()}
    resultado = {}
    for _, row in df.iterrows():
        comp = row.get("COMP.")
        faturamento = row.get("FATURAMENTO")
        if not isinstance(comp, str) or "/" not in comp or pd.isna(faturamento):
            continue
        mes_str, _, ano_str = comp.partition("/")
        mes = mes_map.get(mes_str.strip().lower())
        if mes is None or not ano_str.strip().isdigit():
            continue
        resultado[(int(ano_str.strip()), mes)] = float(faturamento)
    return resultado


# ==============================================================================================
# Cálculo — recalcula VENDAS/BASE/ADICIONAIS do zero (ver docstring do módulo).
# ==============================================================================================

def calcular_adicional10(session, competencia_id: int, faturamento) -> dict:
    """Devolve um dict com vendas, base_calculo, adicional_1, adicional_4, total, e o detalhamento (uma
    linha por NF da competência, com a coluna `conta` indicando se ela entrou ou não em VENDAS)."""
    nfes = carregar_nfes_itens(session, competencia_id)
    clientes = carregar_clientes_filtro(session)

    if nfes.empty:
        vazio = nfes.assign(conta=pd.Series(dtype=bool))
        return {
            "vendas": 0.0, "limite_10pct": 0.0, "base_calculo": 0.0, "adicional_1": 0.0, "adicional_4": 0.0,
            "total": 0.0, "detalhamento": vazio,
        }

    clientes_dedup = clientes.drop_duplicates(subset="cod_cliente", keep="first").copy()
    clientes_dedup["calcula_sim"] = (
        clientes_dedup["calcula"].fillna("").astype(str).str.strip().str.lower().eq("sim")
    )
    mapa_calcula = clientes_dedup.set_index("cod_cliente")["calcula_sim"]

    detalhamento = nfes.copy()
    # .astype(bool) é necessário mesmo depois do fillna(False): quando NENHUM cod_cliente das NFs bate com
    # o cadastro (ex: cadastro ainda vazio — caso real do import via "Report" bruto do Winthor, que não
    # traz FILTRO nenhum), o .map() devolve uma coluna inteira de NaN (float64) e o fillna(False) resultante
    # vira dtype "object" (Python bool solto, não numpy bool) em vez de dtype bool de verdade. Sem o
    # .astype(bool), o "~detalhamento['conta']" mais adiante (nesta função e na página) faz um NOT bit a bit
    # em vez de NOT lógico — ~False vira -1 (não True) — e o .loc[...] tenta usar esses -1 como RÓTULO de
    # linha em vez de máscara booleana, estourando KeyError "None of [...] are in the [index]" (achado em
    # produção em 13/08/2026, ao calcular uma competência cujas NFs vieram só do export bruto, sem cadastro
    # de clientes ainda importado).
    detalhamento["conta"] = detalhamento["cod_cliente"].map(mapa_calcula).fillna(False).astype(bool)

    vendas = float(detalhamento.loc[detalhamento["conta"], "vl_total"].fillna(0).sum())
    faturamento = float(faturamento) if faturamento is not None else 0.0
    limite_10pct = faturamento * PCT_FATURAMENTO_LIMITE / 100
    base_calculo = max(vendas - limite_10pct, 0.0)
    adicional_1 = round((base_calculo * PCT_BASE_ADICIONAL_1 / 100) * ALIQ_ADICIONAL_1 / 100, 2)
    adicional_4 = round((base_calculo * PCT_BASE_ADICIONAL_4 / 100) * ALIQ_ADICIONAL_4 / 100, 2)

    return {
        "vendas": round(vendas, 2),
        "limite_10pct": round(limite_10pct, 2),
        "base_calculo": round(base_calculo, 2),
        "adicional_1": adicional_1,
        "adicional_4": adicional_4,
        "total": round(adicional_1 + adicional_4, 2),
        "detalhamento": detalhamento,
    }
