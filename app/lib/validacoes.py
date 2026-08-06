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

AGRUPAMENTO (pedido do usuário em 06/08/2026: "um mesmo erro pode se repetir, é melhor que ele agrupe"):
cada função monta um dict {chave_agrupamento: {...,"item_ids": [...]}} — uma linha por combinação distinta
do erro (por NCM, ou por parceiro+CFOP) — e delega a gravação para `inconsistencias_util.gravar_grupos`,
que insere o resumo em `inconsistencias` (com `quantidade` = nº de itens) e o vínculo item a item em
`inconsistencia_itens` (usado pela Planilha de Entrada/Saída para sinalizar a linha certa na grade — ver
sql/008_agrupar_inconsistencias.sql)."""
import re
from collections import defaultdict

from sqlalchemy import text

from lib.inconsistencias_util import gravar_grupos


def _normaliza_nome(s: str) -> str:
    s = (s or "").upper()
    s = re.sub(r"^\d+\s*-\s*", "", s)  # remove código do parceiro, ex: "2787 - "
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def gerar_inconsistencias_ncm(session, competencia_id: int, empresa_id: int = None) -> int:
    """Compara, por NCM, o conjunto de classificações ST usadas na Entrada vs na Saída (ignorando itens de
    transferência, que têm regra própria). Gera UM grupo por NCM cujo tratamento diverge entre os dois
    lados (já era agrupado por natureza — 1 NCM só aparece 1 vez mesmo que em centenas de itens; agora
    também guarda TODOS os itens daquele NCM em `inconsistencia_itens`, não só um exemplo). Retorna a
    quantidade de GRUPOS (NCMs) gerados.

    Limpa as inconsistências deste tipo geradas numa rodada anterior desta competência antes de inserir de
    novo — sem isso, clicar em "Calcular apuração" mais de uma vez duplicava as mesmas inconsistências
    (achado em 06/08/2026, junto com a correção equivalente em gerar_inconsistencias_transferencia)."""
    session.execute(text("""
        delete from inconsistencias where competencia_id = :cid and tipo = 'ncm_st_inconsistente'
    """), {"cid": competencia_id})
    session.commit()  # fecha a transação do delete antes do bulk insert (que usa outra conexão do pool)

    rows = session.execute(text("""
        select ni.tipo_operacao, ni.ncm, ni.id, ce.is_st
        from notas_fiscais_itens ni
        join cfop_efetivo ce on ce.codigo = ni.cfop
        where ni.competencia_id = :cid and ce.is_transferencia = false and ni.ncm is not null
    """), {"cid": competencia_id}).mappings().all()

    entrada_st = defaultdict(set)      # ncm -> {True, False} conforme aparece na entrada
    saida_st = defaultdict(set)
    itens_por_ncm = defaultdict(list)  # ncm -> [ids de item, entrada+saída]

    for r in rows:
        alvo = entrada_st if r["tipo_operacao"] == "entrada" else saida_st
        alvo[r["ncm"]].add(bool(r["is_st"]))
        itens_por_ncm[r["ncm"]].append(r["id"])

    grupos = {}
    ncms = set(entrada_st) & set(saida_st)
    for ncm in ncms:
        e_st, s_st = entrada_st[ncm], saida_st[ncm]
        # inconsistente quando os regimes usados nos dois lados não se sobrepõem
        # (ex: entrou só como não-ST mas só saiu como ST, ou vice-versa)
        if e_st.isdisjoint(s_st):
            entrada_regime = "ST" if e_st == {True} else "não-ST" if e_st == {False} else "misto"
            saida_regime = "ST" if s_st == {True} else "não-ST" if s_st == {False} else "misto"
            descricao = (
                f"NCM {ncm}: entrou como {entrada_regime} mas saiu como {saida_regime} — tratamento de "
                f"substituição tributária inconsistente entre Entrada e Saída ({len(itens_por_ncm[ncm])} "
                f"item(ns) de NF com esse NCM nesta competência)."
            )
            grupos[ncm] = {"ncm": ncm, "cfop": None, "descricao": descricao, "item_ids": itens_por_ncm[ncm]}

    return gravar_grupos(session, competencia_id, "ncm_st_inconsistente", grupos, empresa_id)


def gerar_inconsistencias_transferencia(session, competencia_id: int, empresa_id: int = None) -> int:
    """Heurística por nome (ver limitação no docstring do módulo) — agrupa por (parceiro, CFOP): todo
    parceiro que não corresponde por nome a nenhuma empresa cadastrada do grupo vira UM grupo por CFOP
    usado, mesmo que apareça em várias notas fiscais diferentes. Limpa as inconsistências deste tipo de uma
    rodada anterior antes de inserir de novo (mesmo motivo do ajuste em gerar_inconsistencias_ncm)."""
    session.execute(text("""
        delete from inconsistencias where competencia_id = :cid and tipo = 'transferencia_nao_vinculada'
    """), {"cid": competencia_id})
    session.commit()  # fecha a transação do delete antes do bulk insert (que usa outra conexão do pool)

    empresas = session.execute(text("select razao_social from empresas")).scalars().all()
    nomes_grupo = [_normaliza_nome(e) for e in empresas]

    itens = session.execute(text("""
        select ni.id, ni.parceiro, ni.cfop, ni.nf_numero
        from notas_fiscais_itens ni
        join cfop_efetivo ce on ce.codigo = ni.cfop
        where ni.competencia_id = :cid and ce.is_transferencia = true
    """), {"cid": competencia_id}).mappings().all()

    brutos = {}  # chave "parceiro_normalizado|cfop" -> {"cfop", "parceiro", "primeira_nf", "item_ids"}
    for it in itens:
        nome_parceiro = _normaliza_nome(it["parceiro"])
        match = any(
            nome_parceiro and (nome_parceiro in nome_grupo or nome_grupo in nome_parceiro)
            for nome_grupo in nomes_grupo
        )
        if not match:
            chave = f"{nome_parceiro}|{it['cfop']}"
            if chave not in brutos:
                brutos[chave] = {
                    "cfop": it["cfop"], "parceiro": it["parceiro"], "primeira_nf": it["nf_numero"],
                    "item_ids": [],
                }
            brutos[chave]["item_ids"].append(it["id"])

    grupos = {}
    for chave, b in brutos.items():
        n = len(b["item_ids"])
        descricao = (
            f"CFOP {b['cfop']} (transferência): parceiro \"{b['parceiro']}\" não corresponde por nome a "
            f"nenhuma empresa cadastrada do grupo — {n} item(ns) de NF nesta competência (ex: NF "
            f"{b['primeira_nf']}). HEURÍSTICA POR NOME — o relatório de origem não traz o CNPJ do parceiro, "
            f"confirme manualmente antes de agir."
        )
        grupos[chave] = {"ncm": None, "cfop": b["cfop"], "descricao": descricao, "item_ids": b["item_ids"]}

    return gravar_grupos(session, competencia_id, "transferencia_nao_vinculada", grupos, empresa_id)
