"""
Cadastro por empresa dos CFOPs de Entrada que compõem a base de Antecipação na Apuração ICMS PE (regime de
Crédito Presumido) — pedido do usuário em 07/08/2026. Mesmo padrão de app/lib/ncm_tributado.py e
app/lib/cfops_sem_validacao.py (grade editável com num_rows="dynamic": linha nova insere, linha removida
exclui).

`bucket` distingue as duas linhas da Apuração que usam essa base:
- "interna": soma na linha 3.1 (Antecipação dentro do estado, calculada como 1,1% do total das bases).
- "externa": soma na linha 3.2 — mas o VALOR dessa linha não é base × alíquota, vem do Extrato de ICMS
  Antecipado do e-Fisco (ver app/lib/extrato_antecipado_pe.py); a base "externa" daqui entra só no cálculo
  do Crédito Presumido (linha 4.2.01), não na própria linha 3.2.
"""
import pandas as pd
from sqlalchemy import text


def listar_cfops_antecipacao(session, empresa_id: int) -> pd.DataFrame:
    rows = session.execute(text("""
        select cp.id, cp.cfop, c.descricao, cp.bucket, cp.observacao, cp.criado_por_email, cp.created_at
        from cfops_antecipacao_pe cp
        left join cfop c on c.codigo = cp.cfop
        where cp.empresa_id = :eid
        order by cp.bucket, cp.cfop
    """), {"eid": empresa_id}).mappings().all()
    return pd.DataFrame(rows, columns=[
        "id", "cfop", "descricao", "bucket", "observacao", "criado_por_email", "created_at",
    ])


def salvar_cfops_antecipacao(session, empresa_id: int, df_original: pd.DataFrame, df_editado: pd.DataFrame,
                              usuario: dict = None) -> dict:
    """Linha nova (sem `id`) insere, linha removida na grade exclui — mesmo padrão de
    salvar_cfops_sem_validacao (ver app/lib/cfops_sem_validacao.py)."""
    ids_originais = set(df_original["id"].dropna().astype(int)) if not df_original.empty else set()
    ids_editados = set(df_editado["id"].dropna().astype(int)) if "id" in df_editado.columns else set()

    removidos = ids_originais - ids_editados
    for cfop_id in removidos:
        session.execute(text("delete from cfops_antecipacao_pe where id = :id"), {"id": int(cfop_id)})

    incluidos = 0
    novas = df_editado[df_editado["id"].isna()] if "id" in df_editado.columns else df_editado
    usuario = usuario or {}
    for _, row in novas.iterrows():
        cfop_raw = row.get("cfop")
        bucket = row.get("bucket")
        if pd.isna(cfop_raw) or bucket not in ("interna", "externa"):
            continue
        session.execute(text("""
            insert into cfops_antecipacao_pe (empresa_id, cfop, bucket, observacao, criado_por, criado_por_email)
            values (:eid, :cfop, :bucket, :obs, :uid, :email)
            on conflict (empresa_id, cfop) do update
                set bucket = excluded.bucket, observacao = excluded.observacao,
                    criado_por = excluded.criado_por, criado_por_email = excluded.criado_por_email
        """), {
            "eid": empresa_id, "cfop": int(cfop_raw), "bucket": bucket, "obs": row.get("observacao") or None,
            "uid": usuario.get("id"), "email": usuario.get("email"),
        })
        incluidos += 1

    session.commit()
    return {"incluidos": incluidos, "removidos": len(removidos)}


def cfops_por_bucket(session, empresa_id: int) -> dict:
    """{"interna": [cfop, ...], "externa": [cfop, ...]} — usado pelo motor de cálculo
    (app/lib/calculo_icms_pe.py) pra saber quais CFOPs somar em cada linha."""
    rows = session.execute(text(
        "select cfop, bucket from cfops_antecipacao_pe where empresa_id = :eid"
    ), {"eid": empresa_id}).mappings().all()
    resultado = {"interna": [], "externa": []}
    for r in rows:
        resultado[r["bucket"]].append(r["cfop"])
    return resultado
