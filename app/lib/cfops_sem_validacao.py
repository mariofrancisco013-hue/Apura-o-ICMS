"""
Cadastro de CFOPs marcados como "não precisa validar", por empresa — pedido do usuário em 06/08/2026.
Mesmo padrão de app/lib/ncm_tributado.py (grade editável com num_rows="dynamic": linha nova insere, linha
removida na grade exclui).

Usado pelas 3 funções gerar_inconsistencias_* (validacoes.py e ncm_tributado.py) para ignorar itens desses
CFOPs — eles continuam normalmente na Planilha e entram na Apuração, só não disparam mais as validações
automáticas de inconsistência.
"""
import pandas as pd
from sqlalchemy import text


def listar_cfops_sem_validacao(session, empresa_id: int) -> pd.DataFrame:
    rows = session.execute(text("""
        select cv.id, cv.cfop, c.descricao, cv.motivo, cv.criado_por_email, cv.created_at
        from cfops_sem_validacao cv
        left join cfop c on c.codigo = cv.cfop
        where cv.empresa_id = :eid
        order by cv.cfop
    """), {"eid": empresa_id}).mappings().all()
    return pd.DataFrame(rows, columns=["id", "cfop", "descricao", "motivo", "criado_por_email", "created_at"])


def salvar_cfops_sem_validacao(session, empresa_id: int, df_original: pd.DataFrame, df_editado: pd.DataFrame,
                                usuario: dict = None) -> dict:
    """Linha nova (sem `id`) insere, linha removida na grade exclui — `cfop` não é editável depois de
    criado (só `motivo`, via remove+adiciona de novo, mais simples que suportar edição in-place aqui)."""
    ids_originais = set(df_original["id"].dropna().astype(int)) if not df_original.empty else set()
    ids_editados = set(df_editado["id"].dropna().astype(int)) if "id" in df_editado.columns else set()

    removidos = ids_originais - ids_editados
    for cfop_id in removidos:
        session.execute(text("delete from cfops_sem_validacao where id = :id"), {"id": int(cfop_id)})

    incluidos = 0
    novas = df_editado[df_editado["id"].isna()] if "id" in df_editado.columns else df_editado
    usuario = usuario or {}
    for _, row in novas.iterrows():
        cfop_raw = row.get("cfop")
        if pd.isna(cfop_raw):
            continue
        session.execute(text("""
            insert into cfops_sem_validacao (empresa_id, cfop, motivo, criado_por, criado_por_email)
            values (:eid, :cfop, :motivo, :uid, :email)
            on conflict (empresa_id, cfop) do update
                set motivo = excluded.motivo, criado_por = excluded.criado_por,
                    criado_por_email = excluded.criado_por_email
        """), {
            "eid": empresa_id, "cfop": int(cfop_raw), "motivo": row.get("motivo") or None,
            "uid": usuario.get("id"), "email": usuario.get("email"),
        })
        incluidos += 1

    session.commit()
    return {"incluidos": incluidos, "removidos": len(removidos)}


def cfops_excluidos_validacao(session, empresa_id: int) -> list:
    """Lista de códigos de CFOP marcados como "não precisa validar" para esta empresa — usada pelas 3
    funções gerar_inconsistencias_* para ignorar itens desses CFOPs nas checagens automáticas."""
    return session.execute(text(
        "select cfop from cfops_sem_validacao where empresa_id = :eid"
    ), {"eid": empresa_id}).scalars().all()
