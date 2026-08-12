"""
ICMS Substituição Tributária Interestadual — pedido do usuário em 10/08/2026: "vamos criar a aba ICMS
Substituição, o 1º passo que deve ser feito é a comparação do relatorio da rotina 1076 com o relatorio da
Da Sefaz, para analizar e comparar notas, aliquotas, se todas que a sefaz está cobrando estão na 1076 ou
ainda estão pendentes de entrada".

Fontes:
- Relatório da SEFAZ ("lançamentos", exportado do portal, ex: "dadoslancamentos.csv") — o que a SEFAZ está
  cobrando de ICMS ST Interestadual, nota a nota.
- Rotina 1076 do Winthor — o que já está lançado no sistema, item a item (várias linhas por NF).

A comparação é por Nota Fiscal (NF): soma "Calculado" da SEFAZ (só Receita 1031 — confirmado com o usuário
em 10/08/2026, ver mais abaixo) contra a soma de "valor_icms_st" da Rotina 1076, por NF. NF que a SEFAZ está
cobrando mas não aparece na 1076 = pendente de entrada no Winthor. NF que aparece nas duas mas os valores
não batem = divergência a investigar.

Layout confirmado em 10/08/2026 (arquivos reais do usuário: "1076 filial 3.xlsx" e "dadoslancamentos.csv"),
validado cruzando contra a planilha manual de apuração do usuário (aba "SEFAZ") — bateu ao centavo em 5 NFs
diferentes (780595→3993,35 / 168845→1608,25 / 239468→4459,97 / 20354→597,50 e 471,27 respectivamente na
1076 e na SEFAZ).

Receita: o relatório da SEFAZ traz duas receitas diferentes no mesmo export ('1031' e '1023'). Confirmado
com o usuário (pergunta direta, respondida "Sim, só Receita 1031") que só a 1031 é ICMS ST Interestadual —
a 1023 é outro tipo de receita e não entra nesta conferência (mas continua sendo gravada, para referência/
auditoria — ver `salvar_sefaz_lancamentos`, que grava tudo, e `comparar_1076_sefaz`, que filtra na hora de
comparar).

Persistência: confirmado com o usuário ("Salvar por competência (Recomendado)") que esta conferência fica
salva por competência, igual os checkpoints da Rotina 1024/1025 — ver sql/015_icms_st_interestadual.sql.

DOIS LAYOUTS da Rotina 1076 (achado em 11/08/2026, arquivo real do usuário "Relatorio 1076 Atacadão
F3.xls"): o Winthor exporta esse relatório tanto item a item (18 colunas, com produto/NCM — o layout
original suportado) quanto já RESUMIDO por Nota Fiscal (17 colunas, sem produto/NCM mas com fornecedor e
CNPJ do fornecedor). Confirmado que é a mesma fonte de dado: as NFs do arquivo resumido bateram exatas, ao
centavo, contra o total por NF calculado agregando o arquivo item a item já importado da mesma competência.
`parse_rotina_1076` detecta automaticamente qual dos dois é pelo número de colunas e usa o parser certo —
os dois gravam na mesma tabela (rotina_1076_itens), só que o resumido deixa nulas as colunas que só
existem no item a item (produto_codigo, produto_descricao, ncm, num_seq_ent) e vice-versa (fornecedor_*
só existe no resumido) — `formato_origem` registra qual layout gerou cada linha. A comparação por NF
(comparar_1076_sefaz) funciona igual com qualquer um dos dois, porque soma valor_icms_st por NF, e esse
valor é confiável nos dois layouts.
"""
import re

import pandas as pd
from sqlalchemy import text

TOLERANCIA = 0.05  # mesma tolerância usada na Conferência Detalhada (app/lib/conferencia_detalhada_1024.py)

# Pedido do usuário em 11/08/2026: renomear o status "Não cobrado pela SEFAZ" -> "Não localizado na Sefaz"
# (mesmo significado, nome mais claro pro analista). Definido aqui no topo porque é usado tanto por
# comparar_1076_sefaz quanto pela lista de justificativas (ver mais abaixo).
STATUS_NAO_LOCALIZADO = "Não localizado na Sefaz"

# Colunas posicionais do export ITEM A ITEM da Rotina 1076 (sheet "Report", sem cabeçalho, 18 colunas).
COLS_1076_ITEM_RAW = [
    "dt_entrada", "dt_emissao", "dt_selo", "num_seq_ent", "nf_numero", "produto_codigo",
    "produto_descricao", "ncm", "uf", "_col9", "valor_produto", "icms_proprio", "base_st",
    "col13", "aliq_st", "aliq_cheia", "base_st_final", "valor_icms_st",
]

# Colunas posicionais do export RESUMIDO POR NF da Rotina 1076 (mesma sheet "Report", sem cabeçalho, mas
# 17 colunas — sem produto/NCM/sequência de item, com fornecedor e CNPJ do fornecedor no lugar). Colunas
# c7 ("ajuste" — não bate em todas as linhas testadas), c11, c13 e c16 têm semântica não confirmada e
# baixo valor informativo (quase sempre 0) — não são gravadas, mesmo tratamento dado à coluna 9 do layout
# item a item.
COLS_1076_RESUMIDO_RAW = [
    "dt_entrada", "dt_emissao", "dt_selo", "nf_numero", "fornecedor", "fornecedor_cnpj", "uf",
    "_col7", "valor_produto", "icms_proprio", "base_st", "_col11", "aliq_st", "_col13",
    "base_st_final", "valor_icms_st", "_col16",
]

# Colunas finais da tabela rotina_1076_itens (sem competencia_id/id/importado_em, que ficam de fora daqui).
COLS_1076_TABELA = [
    "dt_entrada", "dt_emissao", "dt_selo", "num_seq_ent", "nf_numero", "produto_codigo",
    "produto_descricao", "ncm", "uf", "valor_produto", "icms_proprio", "base_st", "col13",
    "aliq_st", "aliq_cheia", "base_st_final", "valor_icms_st", "formato_origem",
    "fornecedor_codigo", "fornecedor_nome", "fornecedor_cnpj",
]

# Colunas do CSV de lançamentos da SEFAZ (cabeçalho real, separador ";", campos entre aspas) -> nome interno.
COLS_SEFAZ_MAP = {
    "CT-e": "cte", "Emitente": "emitente", "Nota Fiscal": "nf_numero",
    "Data de Inclusão": "data_inclusao", "Data do Fato Gerador": "data_fato_gerador",
    "Valor Total": "valor_total_nota", "Destinatário": "destinatario",
    "Credenciamento": "credenciamento", "Data de Vencimento": "data_vencimento",
    "Receita": "receita", "Calculado": "calculado", "Pago": "pago", "DAE": "dae",
    "Retenção": "retencao", "GNRE": "gnre", "Ressarcimento": "ressarcimento",
    "Crédito Presumido": "credito_presumido", "Parcelado": "parcelado",
    "Auto Infração": "auto_infracao", "N° DAE": "n_dae", "Situação": "situacao",
}
COLS_SEFAZ_TABELA = [
    "cte", "emitente", "nf_numero", "data_inclusao", "data_fato_gerador", "valor_total_nota",
    "destinatario", "credenciamento", "data_vencimento", "receita", "calculado", "pago", "dae",
    "retencao", "gnre", "ressarcimento", "credito_presumido", "parcelado", "auto_infracao",
    "n_dae", "situacao",
]

_BOX_CHARS = re.compile(r"[┌┐└┘│─╞═╡]")


def _limpar_celula_corrompida(valor):
    """Em ~0.7% das linhas do export da Rotina 1076 (achado em 10/08/2026, arquivo real "1076 filial
    3.xlsx"), o Winthor grava, em vez do valor esperado nas colunas num_seq_ent/aliq_cheia, uma mini-tabela
    ASCII com o nome da coluna e os valores empilhados, ex:

        "┌─────────┐\\n│NUMSEQENT│\\n╞═════════╡\\n│4        │\\n│4        │\\n└─────────┘"

    (foi justamente essa corrupção que revelou os nomes reais das colunas "NUMSEQENT" e "ALIQCHEIA",
    vazados pelo Winthor dentro da própria célula quebrada). Estratégia: ignora a linha de cabeçalho (antes
    do separador ╞...╡) e pega a primeira linha de dado de verdade depois dele, limpando os caracteres de
    desenho de caixa. Célula normal (sem corrupção) é devolvida como veio, sem nenhum processamento."""
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return valor
    texto = str(valor)
    if "│" not in texto and "┌" not in texto:
        return valor
    linhas = texto.split("\n")
    idx_sep = next((i for i, l in enumerate(linhas) if "╞" in l), -1)
    linhas_dado = linhas[idx_sep + 1:] if idx_sep >= 0 else linhas
    for linha in linhas_dado:
        limpo = _BOX_CHARS.sub("", linha).strip()
        if limpo:
            return limpo
    return None


def _dividir_codigo_nome(valor):
    """Separa a célula "Fornecedor" do layout resumido da Rotina 1076 ("<código> - <nome>", ex: "254 - IBEL
    IND DE BORRACHA E.V.A. LTDA") em (codigo, nome) — mesmo formato/mesma lógica já usada para a célula
    "Produto" do Entrada/Saída (ver _dividir_codigo_descricao em app/lib/importacao.py)."""
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return None, None
    texto = str(valor).strip()
    if " - " in texto:
        codigo, nome = texto.split(" - ", 1)
        return codigo.strip(), nome.strip()
    return None, texto or None


def _moeda_br_para_float(valor) -> float:
    """Converte string de moeda do export da SEFAZ (ex: "R$ 16.041,00", com espaço normal ou não-quebrável
    entre "R$" e o número) para float. Célula vazia/inválida vira 0.0 — mais seguro que travar a importação
    inteira por causa de um lançamento sem valor num campo secundário (ex: DAE, GNRE)."""
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return 0.0
    texto = str(valor).strip().replace("\xa0", " ")
    texto = texto.replace("R$", "").strip()
    if not texto or texto.lower() in ("nan", "none", "-"):
        return 0.0
    texto = texto.replace(".", "").replace(",", ".")
    try:
        return float(texto)
    except ValueError:
        return 0.0


def _parse_1076_item(df: pd.DataFrame) -> pd.DataFrame:
    """Layout item a item (18 colunas) — uma linha por item de entrada, a mesma NF aparece várias vezes."""
    df = df.copy()
    df.columns = COLS_1076_ITEM_RAW

    df["num_seq_ent"] = df["num_seq_ent"].apply(_limpar_celula_corrompida)
    df["aliq_cheia"] = df["aliq_cheia"].apply(_limpar_celula_corrompida)

    out = pd.DataFrame({"nf_numero": df["nf_numero"].astype(str).str.strip()})
    out["dt_entrada"] = pd.to_datetime(df["dt_entrada"], errors="coerce").dt.date
    out["dt_emissao"] = pd.to_datetime(df["dt_emissao"], errors="coerce").dt.date
    out["dt_selo"] = pd.to_datetime(df["dt_selo"], errors="coerce").dt.date
    out["num_seq_ent"] = df["num_seq_ent"].astype(str)
    out["produto_codigo"] = df["produto_codigo"].astype(str)
    out["produto_descricao"] = df["produto_descricao"]
    out["ncm"] = df["ncm"].astype(str)
    out["uf"] = df["uf"]
    out["valor_produto"] = pd.to_numeric(df["valor_produto"], errors="coerce").fillna(0).astype(float)
    out["icms_proprio"] = pd.to_numeric(df["icms_proprio"], errors="coerce").fillna(0).astype(float)
    out["base_st"] = pd.to_numeric(df["base_st"], errors="coerce").fillna(0).astype(float)
    out["col13"] = pd.to_numeric(df["col13"], errors="coerce").fillna(0).astype(float)
    out["aliq_st"] = pd.to_numeric(df["aliq_st"], errors="coerce").fillna(0).astype(float)
    out["aliq_cheia"] = df["aliq_cheia"].astype(str)
    out["base_st_final"] = pd.to_numeric(df["base_st_final"], errors="coerce").fillna(0).astype(float)
    out["valor_icms_st"] = pd.to_numeric(df["valor_icms_st"], errors="coerce").fillna(0).astype(float)
    out["formato_origem"] = "item"
    out["fornecedor_codigo"] = None
    out["fornecedor_nome"] = None
    out["fornecedor_cnpj"] = None
    return out[COLS_1076_TABELA]


def _parse_1076_resumido(df: pd.DataFrame) -> pd.DataFrame:
    """Layout resumido por NF (17 colunas) — uma linha por Nota Fiscal, sem detalhe de item/produto/NCM."""
    df = df.copy()
    df.columns = COLS_1076_RESUMIDO_RAW

    fornecedor_split = df["fornecedor"].apply(_dividir_codigo_nome)

    out = pd.DataFrame({"nf_numero": df["nf_numero"].astype(str).str.strip()})
    out["dt_entrada"] = pd.to_datetime(df["dt_entrada"], errors="coerce").dt.date
    out["dt_emissao"] = pd.to_datetime(df["dt_emissao"], errors="coerce").dt.date
    out["dt_selo"] = pd.to_datetime(df["dt_selo"], errors="coerce").dt.date
    out["num_seq_ent"] = None
    out["produto_codigo"] = None
    out["produto_descricao"] = None
    out["ncm"] = None
    out["uf"] = df["uf"]
    out["valor_produto"] = pd.to_numeric(df["valor_produto"], errors="coerce").fillna(0).astype(float)
    out["icms_proprio"] = pd.to_numeric(df["icms_proprio"], errors="coerce").fillna(0).astype(float)
    out["base_st"] = pd.to_numeric(df["base_st"], errors="coerce").fillna(0).astype(float)
    out["col13"] = None
    out["aliq_st"] = pd.to_numeric(df["aliq_st"], errors="coerce").fillna(0).astype(float)
    out["aliq_cheia"] = None
    out["base_st_final"] = pd.to_numeric(df["base_st_final"], errors="coerce").fillna(0).astype(float)
    out["valor_icms_st"] = pd.to_numeric(df["valor_icms_st"], errors="coerce").fillna(0).astype(float)
    out["formato_origem"] = "resumido_nf"
    out["fornecedor_codigo"] = fornecedor_split.apply(lambda par: par[0])
    out["fornecedor_nome"] = fornecedor_split.apply(lambda par: par[1])
    out["fornecedor_cnpj"] = df["fornecedor_cnpj"]
    return out[COLS_1076_TABELA]


def parse_rotina_1076(arquivo) -> pd.DataFrame:
    """Lê o export da Rotina 1076 do Winthor (.xls/.xlsx, sheet "Report", sem linha de cabeçalho) e devolve
    um DataFrame já no formato da tabela rotina_1076_itens (sem competencia_id, que é acrescentado por
    salvar_rotina_1076). Detecta automaticamente qual dos dois layouts é pelo número de colunas — ver
    docstring do módulo:
    - 18 colunas: layout item a item (uma linha por item de entrada; a mesma NF aparece várias vezes).
    - 17 colunas: layout resumido por NF (uma linha por Nota Fiscal; sem produto/NCM, com fornecedor).
    A agregação por NF (soma de valor_icms_st) acontece em comparar_1076_sefaz e funciona igual com
    qualquer um dos dois layouts."""
    # engine="calamine": mesmo motivo do resto do projeto (ver app/lib/importacao.py) — mais tolerante a XML
    # fora do padrão que o Winthor às vezes gera em exports .xlsx.
    df = pd.read_excel(arquivo, sheet_name="Report", header=None, engine="calamine")
    if len(df.columns) == len(COLS_1076_ITEM_RAW):
        return _parse_1076_item(df)
    if len(df.columns) == len(COLS_1076_RESUMIDO_RAW):
        return _parse_1076_resumido(df)
    raise ValueError(
        f"Arquivo da Rotina 1076 tem {len(df.columns)} colunas — esperado {len(COLS_1076_ITEM_RAW)} "
        f"(layout item a item) ou {len(COLS_1076_RESUMIDO_RAW)} (layout resumido por NF). O layout do "
        f"export pode ter mudado — confira antes de importar."
    )


def parse_sefaz_lancamentos(arquivo) -> pd.DataFrame:
    """Lê o CSV de lançamentos exportado do portal da SEFAZ (separador ";", campos entre aspas, UTF-8 com
    BOM, valores em moeda no formato brasileiro, datas dd/mm/aaaa) e devolve um DataFrame no formato da
    tabela sefaz_st_lancamentos — TODAS as receitas são gravadas (para referência/auditoria); o filtro pela
    Receita 1031 (ICMS ST Interestadual) acontece em comparar_1076_sefaz, não aqui."""
    df = pd.read_csv(arquivo, sep=";", encoding="utf-8-sig", dtype=str, quotechar='"')
    faltando = [c for c in COLS_SEFAZ_MAP if c not in df.columns]
    if faltando:
        raise ValueError(
            f"Colunas esperadas não encontradas no CSV da SEFAZ: {faltando}. Confira se é o export de "
            f"'lançamentos' certo do portal da SEFAZ — colunas encontradas no arquivo: "
            f"{df.columns.tolist()}"
        )
    df = df.rename(columns=COLS_SEFAZ_MAP)

    out = pd.DataFrame({"nf_numero": df["nf_numero"].astype(str).str.strip()})
    out["cte"] = df["cte"]
    out["emitente"] = df["emitente"]
    out["data_inclusao"] = pd.to_datetime(df["data_inclusao"], format="%d/%m/%Y", errors="coerce").dt.date
    out["data_fato_gerador"] = pd.to_datetime(
        df["data_fato_gerador"], format="%d/%m/%Y", errors="coerce"
    ).dt.date
    out["valor_total_nota"] = df["valor_total_nota"].apply(_moeda_br_para_float)
    out["destinatario"] = df["destinatario"]
    out["credenciamento"] = df["credenciamento"]
    out["data_vencimento"] = pd.to_datetime(df["data_vencimento"], format="%d/%m/%Y", errors="coerce").dt.date
    out["receita"] = df["receita"].astype(str).str.strip()
    out["calculado"] = df["calculado"].apply(_moeda_br_para_float)
    out["pago"] = df["pago"].apply(_moeda_br_para_float)
    out["dae"] = df["dae"].apply(_moeda_br_para_float)
    out["retencao"] = df["retencao"].apply(_moeda_br_para_float)
    out["gnre"] = df["gnre"].apply(_moeda_br_para_float)
    out["ressarcimento"] = df["ressarcimento"].apply(_moeda_br_para_float)
    out["credito_presumido"] = df["credito_presumido"].apply(_moeda_br_para_float)
    out["parcelado"] = df["parcelado"].apply(_moeda_br_para_float)
    out["auto_infracao"] = df["auto_infracao"].apply(_moeda_br_para_float)
    out["n_dae"] = df["n_dae"]
    out["situacao"] = df["situacao"]
    return out[COLS_SEFAZ_TABELA]


def salvar_rotina_1076(session, competencia_id: int, df: pd.DataFrame) -> int:
    """Substitui os itens desta competência pelos recém-importados (apagar+inserir — evita duplicar se o
    analista reimportar o mesmo arquivo, mesmo padrão usado em todo o resto do projeto)."""
    session.execute(text("delete from rotina_1076_itens where competencia_id = :cid"), {"cid": competencia_id})
    if not df.empty:
        out = df.copy()
        out.insert(0, "competencia_id", competencia_id)
        out.to_sql("rotina_1076_itens", session.bind, if_exists="append", index=False, method="multi", chunksize=500)
    session.commit()
    return len(df)


def salvar_sefaz_lancamentos(session, competencia_id: int, df: pd.DataFrame) -> int:
    """Mesmo padrão de salvar_rotina_1076 — apagar+inserir por competência."""
    session.execute(text("delete from sefaz_st_lancamentos where competencia_id = :cid"), {"cid": competencia_id})
    if not df.empty:
        out = df.copy()
        out.insert(0, "competencia_id", competencia_id)
        out.to_sql("sefaz_st_lancamentos", session.bind, if_exists="append", index=False, method="multi", chunksize=500)
    session.commit()
    return len(df)


def carregar_rotina_1076(session, competencia_id: int) -> pd.DataFrame:
    rows = session.execute(text("""
        select dt_entrada, dt_emissao, dt_selo, num_seq_ent, nf_numero, produto_codigo, produto_descricao,
               ncm, uf, valor_produto, icms_proprio, base_st, col13, aliq_st, aliq_cheia, base_st_final,
               valor_icms_st, formato_origem, fornecedor_codigo, fornecedor_nome, fornecedor_cnpj
        from rotina_1076_itens where competencia_id = :cid order by nf_numero, num_seq_ent
    """), {"cid": competencia_id}).mappings().all()
    return pd.DataFrame(rows, columns=COLS_1076_TABELA)


def carregar_sefaz_lancamentos(session, competencia_id: int) -> pd.DataFrame:
    rows = session.execute(text("""
        select cte, emitente, nf_numero, data_inclusao, data_fato_gerador, valor_total_nota, destinatario,
               credenciamento, data_vencimento, receita, calculado, pago, dae, retencao, gnre,
               ressarcimento, credito_presumido, parcelado, auto_infracao, n_dae, situacao
        from sefaz_st_lancamentos where competencia_id = :cid order by nf_numero, receita
    """), {"cid": competencia_id}).mappings().all()
    return pd.DataFrame(rows, columns=COLS_SEFAZ_TABELA)


def comparar_1076_sefaz(session, competencia_id: int, receita_filtro: str = "1031") -> pd.DataFrame:
    """O núcleo da aba **Interestadual**: por NF, compara o que a SEFAZ está cobrando (soma de "calculado",
    só da Receita informada em receita_filtro — '1031' = ICMS ST Interestadual, confirmado com o usuário)
    contra o que já está no sistema (soma de "valor_icms_st" da Rotina 1076 para a mesma NF).

    Modelado diretamente na aba "SEFAZ" da planilha manual de apuração do usuário (colunas NF, CALCULO,
    "ICM A PAGAR (SUPPLY)", DIFERENÇA) — mesma lógica, automatizada.

    Só entram NFs de fora do Ceará (uf != 'CE') — pedido do usuário em 11/08/2026: "o relatorio da sefaz só
    serve para comprar as de fora do estado do Ceara, as internas deve ser tratada com base na informação
    da planilha em anexa" (ver listar_1076_interno, a aba **Interno**, para as NFs de dentro do estado).

    Devolve um DataFrame com uma linha por NF (união das NFs da SEFAZ filtradas + das NFs da 1076 fora do
    Ceará) e as colunas: nf_numero, sefaz_calculado, sistema_valor_icms_st, diferenca, status. status é:
    - "Pendente de entrada": a SEFAZ está cobrando mas a NF não aparece na Rotina 1076 (nem um item) — a
      nota ainda não foi lançada no Winthor.
    - "Divergente": a NF aparece nas duas, mas os valores não batem (fora da TOLERANCIA).
    - "OK": bate (dentro da TOLERANCIA).
    - "Não cobrado pela SEFAZ": a NF aparece na Rotina 1076 mas não tem cobrança da SEFAZ nesta Receita —
      informativo (pode ser NF de outra receita, ou lançamento da SEFAZ ainda não disponibilizado)."""
    sefaz = carregar_sefaz_lancamentos(session, competencia_id)
    rotina = carregar_rotina_1076(session, competencia_id)
    rotina = rotina[rotina["uf"] != "CE"] if not rotina.empty else rotina

    sefaz_filtrado = sefaz[sefaz["receita"] == str(receita_filtro)] if not sefaz.empty else sefaz
    sefaz_agg = (
        sefaz_filtrado.groupby("nf_numero", as_index=False)["calculado"].sum()
        .rename(columns={"calculado": "sefaz_calculado"})
        if not sefaz_filtrado.empty
        else pd.DataFrame(columns=["nf_numero", "sefaz_calculado"])
    )
    rotina_agg = (
        rotina.groupby("nf_numero", as_index=False)["valor_icms_st"].sum()
        .rename(columns={"valor_icms_st": "sistema_valor_icms_st"})
        if not rotina.empty
        else pd.DataFrame(columns=["nf_numero", "sistema_valor_icms_st"])
    )

    comp = sefaz_agg.merge(rotina_agg, on="nf_numero", how="outer")
    comp["sefaz_calculado"] = pd.to_numeric(comp["sefaz_calculado"], errors="coerce")
    comp["sistema_valor_icms_st"] = pd.to_numeric(comp["sistema_valor_icms_st"], errors="coerce")
    comp["diferenca"] = comp["sefaz_calculado"].fillna(0) - comp["sistema_valor_icms_st"].fillna(0)

    def _status(row):
        tem_sefaz = pd.notna(row["sefaz_calculado"])
        tem_sistema = pd.notna(row["sistema_valor_icms_st"])
        if tem_sefaz and not tem_sistema:
            return "Pendente de entrada"
        if not tem_sefaz and tem_sistema:
            return STATUS_NAO_LOCALIZADO
        if abs(row["diferenca"]) > TOLERANCIA:
            return "Divergente"
        return "OK"

    comp["status"] = comp.apply(_status, axis=1)
    comp = comp.sort_values(
        by=["status", "nf_numero"],
        key=lambda s: s if s.name != "status" else s.map(
            {"Pendente de entrada": 0, "Divergente": 1, STATUS_NAO_LOCALIZADO: 2, "OK": 3}
        ),
    ).reset_index(drop=True)
    return comp[["nf_numero", "sefaz_calculado", "sistema_valor_icms_st", "diferenca", "status"]]


# ==============================================================================================
# Justificativa das divergências (aba Interestadual) — pedido do usuário em 11/08/2026: "inclua uma coluna
# justificativa, para as divergências onde o analista deve informar do que se trata a divergência
# [...] Além disso, ao lado deve ter um campo observação que permita a digitação de texto livre. Nas que
# estão como Não cobrados pela Sefaz, mude o nome para Não localizado na Sefaz e na justificativa nota não
# selada ou Outra competência."
#
# Cada status tinha, a princípio, seu próprio conjunto de opções de justificativa (não faz sentido, por
# exemplo, "Sefaz errou no cálculo" pra uma NF que a Sefaz nem cobrou) — por isso duas listas separadas,
# JUSTIFICATIVAS_DIVERGENTE e JUSTIFICATIVAS_NAO_LOCALIZADO. O usuário pediu, ainda em 11/08/2026, pra editar
# tudo direto numa única coluna na tabela principal da aba ("a justificativa e observação e excluída coloque
# para que selecione diretamente aqui" + "a justificativa de nota não selada ou outra competencia deve estar
# nessa aba como uma opção nessa coluna") — como o Streamlit não permite opções condicionais por linha numa
# mesma coluna (SelectboxColumn.options vale pra coluna inteira), a tela usa JUSTIFICATIVAS_TODAS (união das
# duas listas) como opções da coluna única de Justificativa.
# ==============================================================================================

JUSTIFICATIVAS_DIVERGENTE = [
    "Tributação corrigida no Sistema",
    "Solicitação de correção Sefaz",
    "Sefaz errou no cálculo (A Menor)",
    "Sistema não calculou",
    "Outra Competência",
]

JUSTIFICATIVAS_NAO_LOCALIZADO = [
    "Nota não selada",
    "Outra competência",
]

JUSTIFICATIVAS_TODAS = list(dict.fromkeys(JUSTIFICATIVAS_DIVERGENTE + JUSTIFICATIVAS_NAO_LOCALIZADO))

COLS_JUSTIFICATIVAS = ["nf_numero", "justificativa", "observacao", "nao_entra_calculo"]


def _texto_ou_none(valor):
    """Normaliza um valor vindo do st.data_editor pra str "limpa" ou None. Achado em produção (12/08/2026,
    erro real do usuário: "AttributeError ... justificativa = (row.get('justificativa') or '').strip()"):
    célula vazia de SelectboxColumn/TextColumn volta do data_editor como NaN (float), não None — e
    `nan or ""` NÃO cai no fallback, porque bool(nan) é True em Python (é um float não-zero) — daí o
    `.strip()` era chamado num float e quebrava. Esta função trata None, NaN e string igual."""
    if valor is None:
        return None
    if isinstance(valor, float) and pd.isna(valor):
        return None
    texto = str(valor).strip()
    return texto or None


def _bool_ou_false(valor):
    """Mesmo problema do `_texto_ou_none` acima, mas pro CheckboxColumn: célula vazia/NaN não deve virar
    True (bool(nan) é True em Python)."""
    if valor is None:
        return False
    if isinstance(valor, float) and pd.isna(valor):
        return False
    return bool(valor)


def carregar_justificativas(session, competencia_id: int) -> pd.DataFrame:
    rows = session.execute(text("""
        select nf_numero, justificativa, observacao, nao_entra_calculo
        from icms_st_justificativas where competencia_id = :cid order by nf_numero
    """), {"cid": competencia_id}).mappings().all()
    df = pd.DataFrame(rows, columns=COLS_JUSTIFICATIVAS)
    df["nao_entra_calculo"] = df["nao_entra_calculo"].fillna(False).astype(bool)
    return df


def salvar_justificativas(session, competencia_id: int, df: pd.DataFrame, usuario_email: str = None) -> int:
    """Upsert por (competencia_id, nf_numero) — `df` só precisa ter as NFs que o analista editou nesta
    tela (não precisa ser a lista inteira; NFs de fora do `df` não são tocadas). Linha totalmente "vazia"
    (sem justificativa, sem observação, e nao_entra_calculo desmarcado) é APAGADA em vez de gravada — tanto
    pra não acumular lixo no banco quanto pra permitir desmarcar "não entra no cálculo" de volta (senão a
    marcação antiga ficaria presa: um simples "pular" não distingue "nunca marcado" de "desmarcado agora").
    `nao_entra_calculo` (pedido do usuário em 11/08/2026, "colocar uma observação de situação, para
    informar se alguma nota é de outra competência E ela não deve ir para o cálculo"): quando True, a NF
    sai da contagem de Pendente/Divergente/Não localizado nos totais da tela (ver
    app/pages/5_ICMS_Substituicao.py) — cobre qualquer status, não só divergência."""
    n = 0
    for _, row in df.iterrows():
        justificativa = _texto_ou_none(row.get("justificativa"))
        observacao = _texto_ou_none(row.get("observacao"))
        nao_entra_calculo = _bool_ou_false(row.get("nao_entra_calculo"))
        if not justificativa and not observacao and not nao_entra_calculo:
            session.execute(text("""
                delete from icms_st_justificativas where competencia_id = :cid and nf_numero = :nf
            """), {"cid": competencia_id, "nf": row["nf_numero"]})
            continue
        session.execute(text("""
            insert into icms_st_justificativas
                (competencia_id, nf_numero, justificativa, observacao, nao_entra_calculo,
                 atualizado_por_email, atualizado_em)
            values (:cid, :nf, :just, :obs, :nao_entra, :email, now())
            on conflict (competencia_id, nf_numero) do update set
                justificativa = excluded.justificativa, observacao = excluded.observacao,
                nao_entra_calculo = excluded.nao_entra_calculo,
                atualizado_por_email = excluded.atualizado_por_email, atualizado_em = now()
        """), {
            "cid": competencia_id, "nf": row["nf_numero"], "just": justificativa, "obs": observacao,
            "nao_entra": nao_entra_calculo, "email": usuario_email,
        })
        n += 1
    session.commit()
    return n


# ==============================================================================================
# Aba Interno — NFs de dentro do Ceará (uf = 'CE'), pedido do usuário em 11/08/2026.
#
# Investigação (planilha manual "ICMS INTERNO" do usuário, seção "4. OPERAÇÕES INTERNAS", + material de
# apoio "TRIBUTAÇÃO 2024.xlsx" com a tabela de carga líquida do Decreto ICMS Nº 29.560/CE): a princípio
# parecia que a planilha recalculava o ICMS ST com uma fórmula própria (base × "percentual de agregação",
# mais um ajuste quando o fornecedor é Optante do Simples Nacional) — mas conferindo os números batendo
# exato, ficou confirmado que a Rotina 1076 JÁ aplica a alíquota certa na entrada, incluindo o adicional de
# Simples Nacional quando é o caso:
#
#   NF 15525 (CAPY, optante do Simples): base R$ 5.599,70. A planilha calcula ICMS(1) = base × 4,08%
#   (alíquota "normal", mesma categoria/origem) = R$ 228,468, mais um "ajuste (2)" de R$ 167,991 por ser
#   optante do Simples — total R$ 396,459. Ao cruzar com a tabela do Decreto 29.560/CE: 4,08% é a linha
#   "20% - Demais mercadorias, própria estado" da tabela NORMAL; o "ajuste" de 3% é exatamente a linha
#   ADICIONAL da tabela SIMPLES NACIONAL para a mesma categoria/origem (3% × 5.599,70 = R$ 167,991, batendo
#   exato); e 4,08% + 3% = 7,08% é EXATAMENTE a alíquota que a Rotina 1076 já usou nessa NF (aliq_st=7,08).
#   Ou seja: o "ICMS(1) + ajuste(2)" da planilha é só a mesma conta que a 1076 já fez de uma vez só — não
#   falta nada pra calcular por fora.
#
# Por isso, confirmado com o usuário ("traga com a aliquota que consta na 1076"): a aba Interno NÃO
# recalcula nada — só agrupa por NF os itens/linhas da Rotina 1076 com uf = 'CE', do jeito que a planilha
# do usuário agrupa (Fornecedor, NF, Data de Entrada, Base de Cálculo, Alíquota, ICMS ST). O cadastro de
# fornecedores (CNPJ -> Optante do Simples, ver `cadastro_fornecedores_st` / Plan1 da planilha do usuário)
# entra só como informação de apoio/auditoria — não afeta o valor calculado, que já vem pronto da 1076.
# ==============================================================================================

COLS_CADASTRO_FORNECEDORES_ST = ["cnpj", "razao_social", "simples"]


def _achar_cabecalho_cadastro(df: pd.DataFrame):
    """Procura, dentro de um DataFrame já lido (sem header), a linha "CNPJ | RAZÃO SOCIAL | SIMPLES" — as
    primeiras linhas da aba costumam vir em branco/com título, então não dá pra assumir uma posição fixa.
    Devolve o índice da linha, ou None se não achar nesta aba."""
    for i in range(len(df)):
        linha = df.iloc[i].astype(str).str.strip().str.upper().tolist()
        if "CNPJ" in linha and "RAZÃO SOCIAL" in linha:
            return i
    return None


def parse_cadastro_fornecedores_st(arquivo, sheet_name=None) -> pd.DataFrame:
    """Lê o cadastro de fornecedores (CNPJ, Razão Social, Optante do Simples) de uma planilha — pode ser a
    aba "Plan1" da planilha manual "ICMS INTERNO" do usuário, ou um arquivo dedicado só com esse cadastro
    (achado em 11/08/2026: o usuário também manda um arquivo "Simples.xlsx" com a mesma estrutura, mas com
    a aba chamada "Planilha1" em vez de "Plan1" — por isso `sheet_name=None` por padrão: procura o
    cabeçalho "CNPJ | RAZÃO SOCIAL | SIMPLES" em TODAS as abas do arquivo, em vez de exigir um nome fixo).
    Um mesmo CNPJ pode aparecer mais de uma vez (linhas duplicadas/atualizadas na planilha do usuário) —
    fica só a última ocorrência."""
    if sheet_name is not None:
        candidatos = {sheet_name: pd.read_excel(arquivo, sheet_name=sheet_name, header=None, engine="calamine")}
    else:
        candidatos = pd.read_excel(arquivo, sheet_name=None, header=None, engine="calamine")

    aba_achada = None
    idx_cabecalho = None
    for nome_aba, df in candidatos.items():
        idx = _achar_cabecalho_cadastro(df)
        if idx is not None:
            aba_achada, idx_cabecalho = nome_aba, idx
            break
    if aba_achada is None:
        raise ValueError(
            f"Não encontrei o cabeçalho \"CNPJ | RAZÃO SOCIAL | SIMPLES\" em nenhuma aba deste arquivo "
            f"(abas conferidas: {list(candidatos.keys())}) — confira se é a planilha certa (cadastro de "
            f"fornecedores, com Optante do Simples)."
        )
    df = candidatos[aba_achada]

    cabecalho = df.iloc[idx_cabecalho].astype(str).str.strip().str.upper().tolist()
    dados = df.iloc[idx_cabecalho + 1:].copy()
    dados.columns = cabecalho
    idx_cnpj = cabecalho.index("CNPJ")
    idx_razao = cabecalho.index("RAZÃO SOCIAL")
    idx_simples = cabecalho.index("SIMPLES")

    out = pd.DataFrame({"cnpj": dados.iloc[:, idx_cnpj].astype(str).str.strip()})
    out["razao_social"] = dados.iloc[:, idx_razao].astype(str).str.strip()
    out["simples"] = dados.iloc[:, idx_simples].astype(str).str.strip()
    out = out[out["cnpj"].notna() & (out["cnpj"] != "") & (out["cnpj"].str.lower() != "nan")]
    out = out.drop_duplicates(subset="cnpj", keep="last").reset_index(drop=True)
    return out[COLS_CADASTRO_FORNECEDORES_ST]


def salvar_cadastro_fornecedores_st(session, df: pd.DataFrame) -> int:
    """Cadastro GLOBAL (não é por competência, igual o cadastro de CFOP) — upsert por CNPJ: atualiza quem
    já existe, insere quem é novo, sem apagar fornecedores que não vieram nesta importação (a planilha do
    usuário pode não trazer o cadastro inteiro toda vez)."""
    for _, row in df.iterrows():
        session.execute(text("""
            insert into cadastro_fornecedores_st (cnpj, razao_social, simples, atualizado_em)
            values (:cnpj, :razao, :simples, now())
            on conflict (cnpj) do update set
                razao_social = excluded.razao_social, simples = excluded.simples, atualizado_em = now()
        """), {"cnpj": row["cnpj"], "razao": row["razao_social"], "simples": row["simples"]})
    session.commit()
    return len(df)


def listar_cadastro_fornecedores_st(session) -> pd.DataFrame:
    rows = session.execute(text("""
        select cnpj, razao_social, simples from cadastro_fornecedores_st order by razao_social
    """)).mappings().all()
    return pd.DataFrame(rows, columns=COLS_CADASTRO_FORNECEDORES_ST)


def listar_1076_interno(session, competencia_id: int) -> pd.DataFrame:
    """Aba Interno: agrupa por NF os itens/linhas da Rotina 1076 com uf = 'CE' (dentro do Ceará) — sem
    recalcular nada, ver comentário acima do porquê. Devolve uma linha por NF com: nf_numero,
    fornecedor_nome (só preenchido se a NF veio do layout resumido — o layout item a item não traz
    fornecedor), fornecedor_cnpj, dt_entrada (a mais antiga, se houver mais de uma linha), base_st_final
    (soma), aliq_st (média — só é uma alíquota "de verdade" quando todas as linhas da NF usam a mesma;
    aliq_st_uniforme=False sinaliza quando não é o caso, pra não passar uma média enganosa), valor_icms_st
    (soma — o valor final, já correto, vindo direto da 1076) e simples (cruzado com
    cadastro_fornecedores_st pelo CNPJ, só informativo)."""
    rotina = carregar_rotina_1076(session, competencia_id)
    rotina = rotina[rotina["uf"] == "CE"] if not rotina.empty else rotina
    if rotina.empty:
        return pd.DataFrame(columns=[
            "nf_numero", "fornecedor_nome", "fornecedor_cnpj", "dt_entrada", "base_st_final", "aliq_st",
            "aliq_st_uniforme", "valor_icms_st", "simples",
        ])

    def _agg(grupo):
        aliqs = grupo["aliq_st"].dropna().unique()
        return pd.Series({
            "fornecedor_nome": next((v for v in grupo["fornecedor_nome"] if pd.notna(v) and v), None),
            "fornecedor_cnpj": next((v for v in grupo["fornecedor_cnpj"] if pd.notna(v) and v), None),
            "dt_entrada": grupo["dt_entrada"].min(),
            "base_st_final": grupo["base_st_final"].sum(),
            "aliq_st": grupo["aliq_st"].mean(),
            "aliq_st_uniforme": len(aliqs) <= 1,
            "valor_icms_st": grupo["valor_icms_st"].sum(),
        })

    agrupado = rotina.groupby("nf_numero").apply(_agg, include_groups=False).reset_index()

    cadastro = listar_cadastro_fornecedores_st(session)
    if not cadastro.empty:
        agrupado = agrupado.merge(
            cadastro[["cnpj", "simples"]], left_on="fornecedor_cnpj", right_on="cnpj", how="left"
        ).drop(columns=["cnpj"])
    else:
        agrupado["simples"] = None

    return agrupado.sort_values("nf_numero").reset_index(drop=True)[[
        "nf_numero", "fornecedor_nome", "fornecedor_cnpj", "dt_entrada", "base_st_final", "aliq_st",
        "aliq_st_uniforme", "valor_icms_st", "simples",
    ]]
