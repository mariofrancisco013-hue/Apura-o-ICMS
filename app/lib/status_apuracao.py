"""
Status de "apuração válida" — pedido do usuário em 06/08/2026: se a competência já foi calculada e não
tem nenhuma inconsistência pendente, ela está válida; se tiver, isso precisa aparecer sinalizado de cara,
tanto na Visão Geral (Home) quanto no topo da página ICMS Normal — sem precisar entrar na aba
Inconsistências para descobrir.

Regra: inconsistências marcadas como 'revisado' ou 'ignorado' NÃO contam contra a validade — o analista já
tratou delas. Só 'pendente' pesa.
"""
from sqlalchemy import text


def classificar_status(status_calculo: str, n_pendentes: int) -> dict:
    """Versão pura (sem consulta ao banco) — usada quando `n_pendentes` já foi calculado em outra query,
    para não duplicar consulta. Devolve {"valida", "n_pendentes", "nivel", "texto"}; `nivel` é
    "success"/"warning"/"info", pensado para virar st.success/st.warning/st.info direto."""
    if status_calculo != "calculada":
        return {
            "valida": False, "n_pendentes": n_pendentes, "nivel": "info",
            "texto": "Apuração ainda não calculada.",
        }
    if not n_pendentes:
        return {
            "valida": True, "n_pendentes": 0, "nivel": "success",
            "texto": "Apuração válida — nenhuma inconsistência pendente.",
        }
    return {
        "valida": False, "n_pendentes": n_pendentes, "nivel": "warning",
        "texto": f"{n_pendentes} inconsistência(s) pendente(s) — revise na aba Inconsistências antes de "
                 f"considerar esta apuração fechada.",
    }


def status_competencia(session, competencia_id: int, status_calculo: str) -> dict:
    """Busca a contagem de pendentes e classifica — para quem ainda não tem esse número em mãos."""
    n_pendentes = session.execute(text("""
        select count(*) from inconsistencias where competencia_id = :cid and status = 'pendente'
    """), {"cid": competencia_id}).scalar()
    return classificar_status(status_calculo, n_pendentes)
