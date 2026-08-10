"""
Cross-check ad-hoc (não persistido) entre um CFOP divergente na Conferência com a Rotina 1024 e o
relatório Winthor "Relatório de conferência PIS/COFINS e ICMS" (Entrada/Saída), que traz o detalhe
item-a-item filtrado por CFOP.

Pedido do usuário em 10/08/2026: quando aparece divergência de um CFOP na Conferência com a Rotina 1024
("Esta sendo apresentada essa divergência..."), ele quer anexar esse relatório detalhado e ver ONDE está a
diferença (por NF/produto), "para não ficar pesado essa planilha de conferência não precisa ficar salva,
somente apontar a divergência ao ser incluída" — ou seja: nada aqui grava no banco, é só uma comparação
feita na hora, em memória, e descartada no fim do render.

Formato do relatório confirmado com o usuário em 10/08/2026 (título "Relatório de conferência PIS/COFINS e
ICMS - Entrada/Saída"): .xls, aba "Report" (igual aos outros exports Winthor), SEM linha de cabeçalho —
18 colunas, posicionais. Mapeamento validado por conferência aritmética (Vl.B.ICMS × %ICMS / 100 = Vl.ICMS
bateu em 14.503/14.503 linhas de uma amostra real, diferença máxima 0.0) e pelos cabeçalhos que o usuário
enviou em print:

  0  Nº NF                 9  Vl.B.ICMS
  1  Data                 10  %ICMS
  2  Cód.Produto/Descrição 11  Vl. ICMS
  3  CFOP                 12  CST P/C
  4  Quant.               13  Vl.B.PIS/COFINS
  5  Vl.Desc.             14  % PIS
  6  Vl.Merc.             15  Vl. PIS
  7  Vl.Item              16  %COFINS
  8  CST ICMS             17  Vl.COFINS
"""
import pandas as pd
from sqlalchemy import text

COLS_RAW = [
    "nf_numero", "data", "produto", "cfop", "quantidade", "valor_desconto", "valor_mercadoria",
    "valor_item", "cst_icms", "base_icms", "aliq_icms", "valor_icms", "cst_pc", "base_pis_cofins",
    "aliq_pis", "valor_pis", "aliq_cofins", "valor_cofins",
]

TOLERANCIA = 0.05  # mesmo limiar usado na comparação com a Rotina 1024 (aba principal)


def _dividir_codigo_descricao(valor):
    if pd.isna(valor):
        return None, None
    texto = str(valor).strip()
    if " - " in texto:
        codigo, descricao = texto.split(" - ", 1)
        return codigo.strip(), descricao.strip()
    return None, texto or None


def parse_relatorio_conferencia_pc(arquivo):
    """Lê o .xls do "Relatório de conferência PIS/COFINS e ICMS" e devolve um DataFrame agregado por
    (cfop, nf_numero, produto_codigo) — soma base_icms/valor_icms de todas as linhas do mesmo item na
    mesma NF (o export pode trazer mais de uma linha por item). Não grava nada, não recebe competencia_id —
    é só leitura do arquivo enviado."""
    # engine="calamine" (não "xlrd"): o Winthor exporta esse relatório específico às vezes como .xlsx
    # gerado por uma ferramenta chamada "ReportBuilder" que produz XML fora do padrão OOXML (atributos
    # como "WindowWidth" em vez de "windowWidth", data incompleta em docProps/core.xml, etc.) — o openpyxl
    # (usado por engine="xlsx"/padrão do pandas) valida esse XML rigorosamente e trava com TypeError em
    # vários pontos diferentes por causa disso (achado em produção em 10/08/2026, arquivos "1096 f17.xlsx"
    # e "1057 f17.xlsx"). O engine calamine (biblioteca Rust) lê célula por célula sem validar essa parte
    # do XML e funciona tanto no .xlsx malformado quanto no .xls antigo (mesmo motor pros dois formatos).
    df = pd.read_excel(arquivo, sheet_name="Report", header=None, engine="calamine")
    if len(df.columns) != len(COLS_RAW):
        raise ValueError(
            f"Arquivo tem {len(df.columns)} colunas, esperado {len(COLS_RAW)}. O layout deste relatório "
            f"pode ter mudado — confira antes de conferir."
        )
    df.columns = COLS_RAW

    df["nf_numero"] = df["nf_numero"].astype(str).str.strip()
    df["cfop"] = pd.to_numeric(df["cfop"], errors="coerce")
    df = df.dropna(subset=["cfop"])
    df["cfop"] = df["cfop"].astype(int)

    _splits = df["produto"].apply(_dividir_codigo_descricao)
    df["produto_codigo"] = _splits.apply(lambda par: par[0])
    df["produto_descricao"] = _splits.apply(lambda par: par[1])

    df["base_icms"] = pd.to_numeric(df["base_icms"], errors="coerce").fillna(0.0)
    df["valor_icms"] = pd.to_numeric(df["valor_icms"], errors="coerce").fillna(0.0)

    agrupado = df.groupby(["cfop", "nf_numero", "produto_codigo"], dropna=False).agg(
        produto_descricao=("produto_descricao", "first"),
        base_icms_relatorio=("base_icms", "sum"),
        valor_icms_relatorio=("valor_icms", "sum"),
        linhas=("valor_icms", "size"),
    ).reset_index()
    return agrupado


def carregar_sistema_agregado(session, competencia_id, tipo_operacao, cfops):
    """Mesma agregação (cfop, nf_numero, produto_codigo), mas a partir do que já está importado no sistema
    (notas_fiscais_itens), restrito aos CFOPs presentes no relatório enviado — para comparar item a item."""
    if not cfops:
        return pd.DataFrame(columns=["cfop", "nf_numero", "produto_codigo", "base_icms_sistema",
                                      "valor_icms_sistema"])
    placeholders = ", ".join(f":c{i}" for i in range(len(cfops)))
    params = {"cid": competencia_id, "tipo": tipo_operacao}
    params.update({f"c{i}": c for i, c in enumerate(cfops)})
    linhas = session.execute(text(f"""
        select cfop, nf_numero, produto_codigo,
               sum(base_icms) as base_icms_sistema, sum(valor_icms) as valor_icms_sistema
        from notas_fiscais_itens
        where competencia_id = :cid and tipo_operacao = :tipo and cfop in ({placeholders})
        group by cfop, nf_numero, produto_codigo
    """), params).mappings().all()
    out = pd.DataFrame(linhas, columns=["cfop", "nf_numero", "produto_codigo", "base_icms_sistema",
                                         "valor_icms_sistema"])
    # o Postgres devolve sum(numeric) como decimal.Decimal (via psycopg2) — subtrair Decimal de float dá
    # TypeError ("unsupported operand type(s) for -: 'decimal.Decimal' and 'float'") na hora de calcular
    # diff_base/diff_icms lá na frente. Convertendo pra float aqui, logo na leitura, evita o erro (achado
    # em produção em 10/08/2026 — não aparecia nos testes locais porque o SQLite de teste guarda os valores
    # como float puro, sem essa conversão do driver do Postgres).
    out["base_icms_sistema"] = pd.to_numeric(out["base_icms_sistema"], errors="coerce").astype(float)
    out["valor_icms_sistema"] = pd.to_numeric(out["valor_icms_sistema"], errors="coerce").astype(float)
    return out


def comparar_relatorio_com_sistema(session, competencia_id, tipo_operacao, arquivo):
    """Função principal chamada pela tela: lê o relatório detalhado enviado, busca o correspondente já
    importado no sistema (só os CFOPs presentes no relatório) e devolve um DataFrame com uma linha por
    item (nf_numero + produto), marcando onde está a diferença. Nada é gravado — arquivo e resultado vivem
    só na memória deste render."""
    relatorio = parse_relatorio_conferencia_pc(arquivo)
    cfops = sorted(relatorio["cfop"].unique().tolist())
    sistema = carregar_sistema_agregado(session, competencia_id, tipo_operacao, cfops)

    comparado = relatorio.merge(
        sistema, on=["cfop", "nf_numero", "produto_codigo"], how="outer", indicator=True
    )
    comparado["base_icms_relatorio"] = comparado["base_icms_relatorio"].fillna(0.0)
    comparado["valor_icms_relatorio"] = comparado["valor_icms_relatorio"].fillna(0.0)
    comparado["base_icms_sistema"] = comparado["base_icms_sistema"].fillna(0.0)
    comparado["valor_icms_sistema"] = comparado["valor_icms_sistema"].fillna(0.0)

    comparado["diff_base"] = comparado["base_icms_relatorio"] - comparado["base_icms_sistema"]
    comparado["diff_icms"] = comparado["valor_icms_relatorio"] - comparado["valor_icms_sistema"]

    def _situacao(row):
        if row["_merge"] == "left_only":
            return "Só no relatório enviado (não importado no sistema)"
        if row["_merge"] == "right_only":
            return "Só no sistema (não aparece no relatório enviado)"
        if abs(row["diff_base"]) > TOLERANCIA or abs(row["diff_icms"]) > TOLERANCIA:
            return "Valor diferente"
        return "OK"

    comparado["situacao"] = comparado.apply(_situacao, axis=1)
    comparado = comparado.drop(columns=["_merge"])

    divergentes = comparado[comparado["situacao"] != "OK"].sort_values(
        ["cfop", "nf_numero", "produto_codigo"]
    ).reset_index(drop=True)
    return divergentes, comparado
