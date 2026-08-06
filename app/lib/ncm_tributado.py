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

from lib.inconsistencias_util import gravar_grupos


def listar_ncms_tributados(session, empresa_id: int) -> pd.DataFrame:
    """`descricao_oficial` vem da tabela de referência `ncm` (sql/005_ncm.sql, carregada por
    scripts/seed_ncm.py) — vazia se essa tabela ainda não tiver sido carregada, ou se o código não for
    encontrado nela. `descricao` continua sendo um campo livre, para observação própria do analista."""
    rows = session.execute(text("""
        select t.id, t.ncm, t.descricao, n.descricao as descricao_oficial, t.created_at
        from ncms_tributados t
        left join ncm n on n.codigo = t.ncm
        where t.empresa_id = :eid order by t.ncm
    """), {"eid": empresa_id}).mappings().all()
    return pd.DataFrame(rows, columns=["id", "ncm", "descricao", "descricao_oficial", "created_at"])


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
    session.commit()  # fecha a transação do delete antes do bulk insert (que usa outra conexão do pool)

    itens = session.execute(text("""
        select ni.id, ni.ncm, ni.cfop, ce.is_st
        from notas_fiscais_itens ni
        join cfop_efetivo ce on ce.codigo = ni.cfop
        where ni.competencia_id = :cid and ni.ncm is not null and ce.is_transferencia = false
    """), {"cid": competencia_id}).mappings().all()

    # Monta os grupos em memória (Python puro, sem ida ao banco) e insere tudo de uma vez no fim — achado
    # em 06/08/2026: com um INSERT síncrono por item classificado, "Calcular apuração" ficava lento em
    # relatórios de Saída com dezenas de milhares de linhas (mesmo problema já corrigido na importação em
    # 05/08, só que essa validação, adicionada depois, tinha ficado de fora daquela correção).
    # Agrupamento (pedido em 06/08/2026, "um mesmo erro pode se repetir, é melhor que ele agrupe"):
    # "ncm_tributado_como_st" agrupa por NCM+CFOP; "ncm_tributado_novo" já era por NCM só (um candidato novo
    # não precisa repetir por CFOP) — a diferença agora é que TODOS os itens de cada grupo ficam vinculados
    # em inconsistencia_itens, não só um exemplo.
    grupos_st, grupos_novo = {}, {}
    for it in itens:
        ncm = it["ncm"]
        if ncm in cadastrados:
            if it["is_st"]:
                chave = f"{ncm}|{it['cfop']}"
                if chave not in grupos_st:
                    grupos_st[chave] = {"ncm": ncm, "cfop": it["cfop"], "item_ids": []}
                grupos_st[chave]["item_ids"].append(it["id"])
        elif not it["is_st"]:
            if ncm not in grupos_novo:
                grupos_novo[ncm] = {"ncm": ncm, "cfop": it["cfop"], "item_ids": []}
            grupos_novo[ncm]["item_ids"].append(it["id"])

    for g in grupos_st.values():
        n = len(g["item_ids"])
        g["descricao"] = (
            f"NCM {g['ncm']} está cadastrado como tributado (não-ST) nesta empresa, mas apareceu no CFOP "
            f"{g['cfop']}, classificado como ST, em {n} item(ns) de NF nesta competência. Confira se o CFOP "
            f"do lançamento está certo ou se este produto mudou de regime tributário."
        )
    for g in grupos_novo.values():
        n = len(g["item_ids"])
        g["descricao"] = (
            f"NCM {g['ncm']} apareceu como tributado (não-ST) em {n} item(ns) de NF (ex: CFOP {g['cfop']}), "
            f"mas ainda não está cadastrado na lista de NCMs tributados desta empresa. Confirme se deve ser "
            f"incluído — aba 'NCMs Tributados' em ICMS Normal."
        )

    n1 = gravar_grupos(session, competencia_id, "ncm_tributado_como_st", grupos_st, empresa_id)
    n2 = gravar_grupos(session, competencia_id, "ncm_tributado_novo", grupos_novo, empresa_id)
    return n1 + n2
