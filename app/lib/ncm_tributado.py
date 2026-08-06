"""
Cadastro de NCMs "tributados" (não-ST — geram crédito/débito pleno) por empresa, e a validação cruzada
que usa esse cadastro. Pedido do usuário em 06/08/2026: os NCMs que ele sabe que são "de fato tributados"
(exemplo: tesouras, roupas, calçados — NCM 82119390, 61069000, etc., esperados nos CFOPs 1102/1202/5102/
6102/5927) devem ficar cadastrados por empresa, com permissão de incluir/excluir pela tela, e o sistema
deve sinalizar duas situações para o analista revisar (não decide sozinho, só aponta):

1. Um NCM cadastrado aqui apareceu num item classificado como ST — pode ser erro de CFOP no lançamento,
   ou o produto pode ter deixado de ser tributado (mudança de regime) e o cadastro precisa ser atualizado.
2. Um NCM que NÃO está cadastrado apareceu como não-ST (tributado) — "candidato novo": pode ser um NCM que
   realmente deveria entrar na lista, mas quem decide é o analista (por isso não é adicionado sozinho).
"""
import pandas as pd
from sqlalchemy import text


def listar_ncms_tributados(session, empresa_id: int) -> pd.DataFrame:
    rows = session.execute(text("""
        select id, ncm, descricao, created_at from ncms_tributados
        where empresa_id = :eid order by ncm
    """), {"eid": empresa_id}).mappings().all()
    return pd.DataFrame(rows, columns=["id", "ncm", "descricao", "created_at"])


def salvar_ncms_tributados(session, empresa_id: int, df_original: pd.DataFrame, df_editado: pd.DataFrame) -> dict:
    """Grade editável com `num_rows="dynamic"` no Streamlit: linhas novas (sem `id`) são inseridas, linhas
    removidas (id que sumiu) são excluídas, o resto é ignorado (o NCM em si não é editável depois de
    criado — só descrição). Retorna {"incluidos": n, "removidos": n}."""
    ids_originais = set(df_original["id"].dropna().astype(int)) if not df_original.empty else set()
    ids_editados = set(df_editado["id"].dropna().astype(int)) if "id" in df_editado.columns else set()

    removidos = ids_originais - ids_editados
    for ncm_id in removidos:
        session.execute(text("delete from ncms_tributados where id = :id"), {"id": int(ncm_id)})

    incluidos = 0
    novas = df_editado[df_editado["id"].isna()] if "id" in df_editado.columns else df_editado
    for _, row in novas.iterrows():
        ncm = str(row.get("ncm") or "").strip()
        if not ncm:
            continue
        session.execute(text("""
            insert into ncms_tributados (empresa_id, ncm, descricao) values (:eid, :ncm, :desc)
            on conflict (empresa_id, ncm) do update set descricao = excluded.descricao
        """), {"eid": empresa_id, "ncm": ncm, "desc": row.get("descricao") or None})
        incluidos += 1

    session.commit()
    return {"incluidos": incluidos, "removidos": len(removidos)}


def gerar_inconsistencias_ncm_tributado(session, competencia_id: int, empresa_id: int) -> int:
    """Roda as duas checagens descritas no docstring do módulo. Limpa as inconsistências desses dois tipos
    geradas numa rodada anterior desta competência antes de inserir de novo — evita duplicar a cada vez
    que o analista clica em "Calcular apuração"."""
    cadastrados = set(session.execute(
        text("select ncm from ncms_tributados where empresa_id = :eid"), {"eid": empresa_id}
    ).scalars().all())

    session.execute(text("""
        delete from inconsistencias where competencia_id = :cid
        and tipo in ('ncm_tributado_como_st', 'ncm_tributado_novo')
    """), {"cid": competencia_id})

    itens = session.execute(text("""
        select ni.id, ni.ncm, ni.cfop, ce.is_st
        from notas_fiscais_itens ni
        join cfop_efetivo ce on ce.codigo = ni.cfop
        where ni.competencia_id = :cid and ni.ncm is not null and ce.is_transferencia = false
    """), {"cid": competencia_id}).mappings().all()

    gerados = 0
    ja_sinalizado_novo = set()  # um NCM "novo" só gera 1 inconsistência por competência, mesmo em várias NFs
    for it in itens:
        ncm = it["ncm"]
        if ncm in cadastrados:
            if it["is_st"]:
                descricao = (
                    f"NCM {ncm} está cadastrado como tributado (não-ST) nesta empresa, mas apareceu no "
                    f"CFOP {it['cfop']}, classificado como ST. Confira se o CFOP do lançamento está certo "
                    f"ou se este produto mudou de regime tributário."
                )
                session.execute(text("""
                    insert into inconsistencias (competencia_id, tipo, ncm, cfop, nf_item_id, descricao)
                    values (:cid, 'ncm_tributado_como_st', :ncm, :cfop, :item_id, :descricao)
                """), {"cid": competencia_id, "ncm": ncm, "cfop": it["cfop"], "item_id": it["id"],
                       "descricao": descricao})
                gerados += 1
        elif not it["is_st"] and ncm not in ja_sinalizado_novo:
            ja_sinalizado_novo.add(ncm)
            descricao = (
                f"NCM {ncm} apareceu como tributado (não-ST) no CFOP {it['cfop']}, mas ainda não está "
                f"cadastrado na lista de NCMs tributados desta empresa. Confirme se deve ser incluído — "
                f"aba 'NCMs Tributados' em ICMS Normal."
            )
            session.execute(text("""
                insert into inconsistencias (competencia_id, tipo, ncm, cfop, nf_item_id, descricao)
                values (:cid, 'ncm_tributado_novo', :ncm, :cfop, :item_id, :descricao)
            """), {"cid": competencia_id, "ncm": ncm, "cfop": it["cfop"], "item_id": it["id"],
                   "descricao": descricao})
            gerados += 1
    session.commit()
    return gerados
