"""
Validações cruzadas (geram registros em `inconsistencias` para a equipe revisar na tela de Inconsistências).

1. NCM x ST: um NCM tratado como "não-ST" (gera crédito/débito pleno) na Entrada deveria também aparecer
   como "não-ST" na Saída, e vice-versa. Regra dada pelo usuário em 05/08/2026 (exemplo: NCM de tesouras,
   8213, tributado normal na Entrada deve estar debitando normal na Saída).

2. Transferência entre empresas não vinculadas: todo item com CFOP `is_transferencia = true` deveria ser
   entre empresas que compartilham a raiz do CNPJ (ver claude/empresas-grupo.md no projeto).

   LIMITAÇÃO CONHECIDA (05/08/2026): os relatórios de Entrada/Saída só trazem o parceiro como texto livre
   "<código> - <razão social>" (ex: "2787 - RIO BRANCO S.A"), sem o CNPJ. Não dá para cruzar com
   segurança contra `empresas.cnpj_raiz` sem o CNPJ do parceiro. Por ora, essa validação faz correspondência
   por NOME (heurística) contra a lista de empresas do grupo, e todo resultado é marcado como heurístico —
   precisa de confirmação manual, e o ideal é depois importar um relatório de cadastro de parceiros que
   traga o CNPJ para tornar isso exato.
"""
import re
from sqlalchemy import text


def _normaliza_nome(s: str) -> str:
    s = (s or "").upper()
    s = re.sub(r"^\d+\s*-\s*", "", s)  # remove código do parceiro, ex: "2787 - "
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def gerar_inconsistencias_ncm(session, competencia_id: int) -> int:
    """Compara, por NCM, o conjunto de classificações ST usadas na Entrada vs na Saída (ignorando itens de
    transferência, que têm regra própria). Gera uma inconsistência por NCM cujo tratamento diverge entre os
    dois lados. Retorna a quantidade de inconsistências geradas.

    Limpa as inconsistências deste tipo geradas numa rodada anterior desta competência antes de inserir de
    novo — sem isso, clicar em "Calcular apuração" mais de uma vez duplicava as mesmas inconsistências
    (achado em 06/08/2026, junto com a correção equivalente em gerar_inconsistencias_transferencia)."""
    session.execute(text("""
        delete from inconsistencias where competencia_id = :cid and tipo = 'ncm_st_inconsistente'
    """), {"cid": competencia_id})

    rows = session.execute(text("""
        select ni.tipo_operacao, ni.ncm, ni.id, ce.is_st
        from notas_fiscais_itens ni
        join cfop_efetivo ce on ce.codigo = ni.cfop
        where ni.competencia_id = :cid and ce.is_transferencia = false and ni.ncm is not null
    """), {"cid": competencia_id}).mappings().all()

    from collections import defaultdict
    entrada_st = defaultdict(set)   # ncm -> {True, False} conforme aparece na entrada
    saida_st = defaultdict(set)
    exemplo_item = {}

    for r in rows:
        alvo = entrada_st if r["tipo_operacao"] == "entrada" else saida_st
        alvo[r["ncm"]].add(bool(r["is_st"]))
        exemplo_item.setdefault((r["tipo_operacao"], r["ncm"]), r["id"])

    gerados = 0
    ncms = set(entrada_st) & set(saida_st)
    for ncm in ncms:
        e_st, s_st = entrada_st[ncm], saida_st[ncm]
        # inconsistente quando os regimes usados nos dois lados não se sobrepõem
        # (ex: entrou só como não-ST mas só saiu como ST, ou vice-versa)
        if e_st.isdisjoint(s_st):
            entrada_regime = "ST" if e_st == {True} else "não-ST" if e_st == {False} else "misto"
            saida_regime = "ST" if s_st == {True} else "não-ST" if s_st == {False} else "misto"
            descricao = (
                f"NCM {ncm}: entrou como {entrada_regime} mas saiu como {saida_regime} — "
                f"tratamento de substituição tributária inconsistente entre Entrada e Saída."
            )
            session.execute(text("""
                insert into inconsistencias (competencia_id, tipo, ncm, nf_item_id, descricao)
                values (:cid, 'ncm_st_inconsistente', :ncm, :item_id, :descricao)
            """), {
                "cid": competencia_id, "ncm": ncm,
                "item_id": exemplo_item.get(("entrada", ncm)) or exemplo_item.get(("saida", ncm)),
                "descricao": descricao,
            })
            gerados += 1
    session.commit()
    return gerados


def gerar_inconsistencias_transferencia(session, competencia_id: int) -> int:
    """Heurística por nome (ver limitação no docstring do módulo) — todo item de transferência cujo
    parceiro não corresponde por nome a nenhuma empresa cadastrada do grupo é sinalizado para revisão
    manual. Limpa as inconsistências deste tipo de uma rodada anterior antes de inserir de novo (mesmo
    motivo do ajuste em gerar_inconsistencias_ncm)."""
    session.execute(text("""
        delete from inconsistencias where competencia_id = :cid and tipo = 'transferencia_nao_vinculada'
    """), {"cid": competencia_id})

    empresas = session.execute(text("select razao_social from empresas")).scalars().all()
    nomes_grupo = [_normaliza_nome(e) for e in empresas]

    itens = session.execute(text("""
        select ni.id, ni.parceiro, ni.cfop, ni.nf_numero
        from notas_fiscais_itens ni
        join cfop_efetivo ce on ce.codigo = ni.cfop
        where ni.competencia_id = :cid and ce.is_transferencia = true
    """), {"cid": competencia_id}).mappings().all()

    gerados = 0
    for it in itens:
        nome_parceiro = _normaliza_nome(it["parceiro"])
        match = any(
            nome_parceiro and (nome_parceiro in nome_grupo or nome_grupo in nome_parceiro)
            for nome_grupo in nomes_grupo
        )
        if not match:
            descricao = (
                f"NF {it['nf_numero']}, CFOP {it['cfop']} (transferência): parceiro \"{it['parceiro']}\" "
                f"não corresponde por nome a nenhuma empresa cadastrada do grupo. HEURÍSTICA POR NOME — "
                f"o relatório de origem não traz o CNPJ do parceiro, confirme manualmente antes de agir."
            )
            session.execute(text("""
                insert into inconsistencias (competencia_id, tipo, cfop, nf_item_id, descricao)
                values (:cid, 'transferencia_nao_vinculada', :cfop, :item_id, :descricao)
            """), {"cid": competencia_id, "cfop": it["cfop"], "item_id": it["id"], "descricao": descricao})
            gerados += 1
    session.commit()
    return gerados
