"""
Leitura do PDF "Extrato de Notas Fiscais Relativas a Operações Interestaduais Sujeitas ao ICMS Antecipado"
(e-Fisco/PE) — usado pela Apuração ICMS PE (regime de Crédito Presumido do atacadista, pedido do usuário
em 07/08/2026).

O que interessa pro cálculo não é a lista nota a nota (quadro I), e sim o quadro final "Resumo do Grupo de
Mercadorias para Extrato dos itens COBRADOS", que já vem totalizado por grupo:

    Grupo de Mercadorias              ICMS com Direito a crédito   ICMS Devido
    ANTECIPACAO                       Sim                          31.904,08
    ANTECIPACAO-ALIQ>17%              Sim                          151,17
    SUBST.TRIB(COSMET.)               Sim                          1.946,04
    SUBST.TRIB(AUTOPECA)              Não                          27,69
    SUBST.TRIB (ELETRO)               Sim                          62,36

A linha "3.2 - Antecipação [alíquota] fora do estado" da Apuração ICMS PE soma o "ICMS Devido" só dos
grupos com "Direito a crédito" = Sim (confirmado com o usuário em 07/08/2026, e batendo exato — ao
centavo — contra a planilha real de apuração da Ultra Comércio de 06/2026: 31.904,08 + 151,17 + 1.946,04 +
62,36 = 34.063,65).
"""
import re

import pdfplumber

# "<Grupo de Mercadoria, pode ter espaços/parênteses/'>'> <Sim|Não> <valor com milhar e 2 decimais>"
_LINHA_GRUPO_RE = re.compile(r"^(.+?)\s+(Sim|Não)\s+([\d.]+,\d{2})$")

_INICIO_RESUMO = "Resumo do Grupo de Mercadorias"
# fim do quadro: primeira linha que não é mais "grupo valor" (observações, "Obs:", etc.)
_FIM_RESUMO_MARCADORES = ("Obs:", "Observações", "RESUMO GERAL")


def _para_decimal(s: str):
    from decimal import Decimal
    return Decimal(s.replace(".", "").replace(",", "."))


def parse_extrato_antecipado(arquivo) -> list[dict]:
    """`arquivo` é um caminho ou um buffer tipo st.file_uploader (PDF do e-Fisco/PE). Devolve uma lista de
    dicts {grupo_mercadoria, direito_credito (bool), icms_devido (Decimal)} — um por linha do quadro
    "Resumo do Grupo de Mercadorias para Extrato dos itens COBRADOS". Lança ValueError se esse quadro não
    for encontrado (arquivo no layout errado, ou é o quadro II — itens NÃO cobrados — que este parser não
    lê, já que não gera ICMS devido)."""
    grupos = []
    dentro_do_resumo = False
    with pdfplumber.open(arquivo) as pdf:
        for page in pdf.pages:
            texto = page.extract_text() or ""
            for linha in texto.split("\n"):
                linha = linha.strip()
                if _INICIO_RESUMO in linha:
                    dentro_do_resumo = True
                    continue
                if not dentro_do_resumo:
                    continue
                if linha.startswith(_FIM_RESUMO_MARCADORES):
                    dentro_do_resumo = False
                    continue
                if linha.startswith("Grupo de Mercadorias"):
                    continue  # cabeçalho do quadro
                m = _LINHA_GRUPO_RE.match(linha)
                if not m:
                    continue
                grupos.append({
                    "grupo_mercadoria": m.group(1).strip(),
                    "direito_credito": m.group(2) == "Sim",
                    "icms_devido": _para_decimal(m.group(3)),
                })
    if not grupos:
        raise ValueError(
            "Não encontrei o quadro 'Resumo do Grupo de Mercadorias para Extrato dos itens COBRADOS' "
            "neste PDF. Confira se é o Extrato de ICMS Antecipado certo (e-Fisco/PE) e se tem itens "
            "cobrados no período (quadro II, de itens NÃO cobrados, não é lido por este parser)."
        )
    return grupos


def salvar_extrato_antecipado(session, competencia_id: int, grupos: list[dict]) -> int:
    """Substitui os grupos desta competência pelos recém-importados (apagar+inserir, igual o padrão usado
    pra checkpoints_referencia — evita duplicar se o analista reimportar o mesmo PDF)."""
    from sqlalchemy import text

    session.execute(
        text("delete from extrato_antecipado_pe where competencia_id = :cid"), {"cid": competencia_id}
    )
    for g in grupos:
        session.execute(text("""
            insert into extrato_antecipado_pe (competencia_id, grupo_mercadoria, direito_credito, icms_devido)
            values (:cid, :grupo, :direito, :icms)
        """), {
            "cid": competencia_id, "grupo": g["grupo_mercadoria"], "direito": g["direito_credito"],
            "icms": float(g["icms_devido"]),
        })
    session.commit()
    return len(grupos)


def listar_extrato_antecipado(session, competencia_id: int):
    """DataFrame com os grupos importados desta competência — pra mostrar na tela e conferir contra o PDF."""
    import pandas as pd
    from sqlalchemy import text

    rows = session.execute(text("""
        select grupo_mercadoria, direito_credito, icms_devido
        from extrato_antecipado_pe
        where competencia_id = :cid
        order by grupo_mercadoria
    """), {"cid": competencia_id}).mappings().all()
    return pd.DataFrame(rows, columns=["grupo_mercadoria", "direito_credito", "icms_devido"])


def total_antecipacao_externa(session, competencia_id: int):
    """Soma de icms_devido só dos grupos com direito_credito=true — é o valor da linha '3.2 - Antecipação
    ... fora do estado' da Apuração ICMS PE (ver docstring do módulo)."""
    from sqlalchemy import text

    total = session.execute(text("""
        select coalesce(sum(icms_devido), 0) from extrato_antecipado_pe
        where competencia_id = :cid and direito_credito = true
    """), {"cid": competencia_id}).scalar()
    from decimal import Decimal
    return Decimal(str(total))
