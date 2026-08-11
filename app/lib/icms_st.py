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
"""
import re

import pandas as pd
from sqlalchemy import text

TOLERANCIA = 0.05  # mesma tolerância usada na Conferência Detalhada (app/lib/conferencia_detalhada_1024.py)

# Colunas posicionais do export da Rotina 1076 (sheet "Report", sem cabeçalho) — ver docstring do módulo.
COLS_1076_RAW = [
    "dt_entrada", "dt_emissao", "dt_selo", "num_seq_ent", "nf_numero", "produto_codigo",
    "produto_descricao", "ncm", "uf", "_col9", "valor_produto", "icms_proprio", "base_st",
    "col13", "aliq_st", "aliq_cheia", "base_st_final", "valor_icms_st",
]
COLS_1076_TABELA = [
    "dt_entrada", "dt_emissao", "dt_selo", "num_seq_ent", "nf_numero", "produto_codigo",
    "produto_descricao", "ncm", "uf", "valor_produto", "icms_proprio", "base_st", "col13",
    "aliq_st", "aliq_cheia", "base_st_final", "valor_icms_st",
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


def parse_rotina_1076(arquivo) -> pd.DataFrame:
    """Lê o export da Rotina 1076 do Winthor (.xls/.xlsx, sheet "Report", sem linha de cabeçalho, 18
    colunas posicionais) e devolve um DataFrame já no formato da tabela rotina_1076_itens (sem
    competencia_id, que é acrescentado por salvar_rotina_1076). Uma linha por item de entrada — a mesma NF
    aparece várias vezes, uma por item; a agregação por NF acontece em comparar_1076_sefaz."""
    # engine="calamine": mesmo motivo do resto do projeto (ver app/lib/importacao.py) — mais tolerante a XML
    # fora do padrão que o Winthor às vezes gera em exports .xlsx.
    df = pd.read_excel(arquivo, sheet_name="Report", header=None, engine="calamine")
    if len(df.columns) != len(COLS_1076_RAW):
        raise ValueError(
            f"Arquivo da Rotina 1076 tem {len(df.columns)} colunas, esperado {len(COLS_1076_RAW)}. O "
            f"layout do export pode ter mudado — confira antes de importar."
        )
    df.columns = COLS_1076_RAW

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
    return out[COLS_1076_TABELA]


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
               valor_icms_st
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
    """O núcleo do 1º passo pedido pelo usuário: por NF, compara o que a SEFAZ está cobrando (soma de
    "calculado", só da Receita informada em receita_filtro — '1031' = ICMS ST Interestadual, confirmado com
    o usuário) contra o que já está no sistema (soma de "valor_icms_st" da Rotina 1076 para a mesma NF).

    Modelado diretamente na aba "SEFAZ" da planilha manual de apuração do usuário (colunas NF, CALCULO,
    "ICM A PAGAR (SUPPLY)", DIFERENÇA) — mesma lógica, automatizada.

    Devolve um DataFrame com uma linha por NF (união das NFs da SEFAZ filtradas + das NFs da 1076) e as
    colunas: nf_numero, sefaz_calculado, sistema_valor_icms_st, diferenca, status. status é:
    - "Pendente de entrada": a SEFAZ está cobrando mas a NF não aparece na Rotina 1076 (nem um item) — a
      nota ainda não foi lançada no Winthor.
    - "Divergente": a NF aparece nas duas, mas os valores não batem (fora da TOLERANCIA).
    - "OK": bate (dentro da TOLERANCIA).
    - "Não cobrado pela SEFAZ": a NF aparece na Rotina 1076 mas não tem cobrança da SEFAZ nesta Receita —
      informativo (pode ser NF de outra receita, ou lançamento da SEFAZ ainda não disponibilizado)."""
    sefaz = carregar_sefaz_lancamentos(session, competencia_id)
    rotina = carregar_rotina_1076(session, competencia_id)

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
            return "Não cobrado pela SEFAZ"
        if abs(row["diferenca"]) > TOLERANCIA:
            return "Divergente"
        return "OK"

    comp["status"] = comp.apply(_status, axis=1)
    comp = comp.sort_values(
        by=["status", "nf_numero"],
        key=lambda s: s if s.name != "status" else s.map(
            {"Pendente de entrada": 0, "Divergente": 1, "Não cobrado pela SEFAZ": 2, "OK": 3}
        ),
    ).reset_index(drop=True)
    return comp[["nf_numero", "sefaz_calculado", "sistema_valor_icms_st", "diferenca", "status"]]
