"""
Cálculo da Apuração ICMS PE — regime de Crédito Presumido do atacadista (Decreto de PE), pedido do usuário
em 07/08/2026. Primeira empresa: Ultra Comércio Atacadista (filial Recife, CNPJ 38.184.070/0002-09).

Modelo bem diferente do ICMS Normal (app/lib/calculo_icms_normal.py): em vez de débito/crédito por CFOP
is_st, gira em torno de três blocos —

  3. Antecipação        = 3.1 (1,1% dentro do estado) + 3.2 (fora do estado, vem do Extrato e-Fisco)
  1. Créditos Totais    = créditos de entrada (líquidos de devolução) + antecipação recolhida no mês anterior
  2. Débitos Totais     = débitos de saída (líquidos de devolução de compra)
  4. Crédito Presumido  = Alíquota Média × Base de Cálculo − Deduções (Antecipação + Créditos Entradas)
  5. Valor a Recolher   = 2. − 1. − 4.
  6/7. Saldo Anterior / Valor Recolher Atual (encadeado com a competência anterior)

Todas as fórmulas abaixo foram reverse-engineered e conferidas ao CENTAVO em 07/08/2026 contra três fontes
independentes da Ultra Comércio, competência 06/2026: a planilha real de apuração PE do usuário (aba
"06.2025" — rótulo da aba está errado, é a competência 06/2026 mesmo, confirmado cruzando os valores), o PDF
da Rotina 1024 (RAICMS Modelo P9) e o PDF do Extrato de ICMS Antecipado do e-Fisco/PE. Toda fórmula abaixo
bateu exatamente, EXCETO a linha 4.1.01 (ver observação nela).

Fonte dos dados por CFOP: Rotina 1024 (checkpoints_referencia, fonte='rotina_1024'), reaproveitando o mesmo
PDF/parser do ICMS Normal (app/lib/importar_1024.py) — mas aqui usamos a coluna "Valores Contábeis"
(valor_contabil) como base das linhas de Antecipação (confirmado batendo exato contra a planilha real), não
"Base de Cálculo" (valor_base, usada pelo ICMS Normal).

Classificação dinâmica de CFOP "devolução": descrição começando com "DEV" (ex: 1202 "DEVOLUCAO DE VENDA
ESTADUAL...", 6202 "DEV. DE COMPRA PARA COMERCIALIZACAO" — mesmo padrão confirmado em sql/002_seed_cfop.sql
e já usado no ICMS Normal). No lado Entrada, essas CFOPs de devolução são ainda separadas em duas linhas
conforme is_st (ver linha 1.1/1.4/1.5 abaixo).
"""
import json
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import text

from lib.calculo_icms_normal import LinhaApuracao, salvar_apuracao  # reaproveitado, mesma tabela genérica
from lib.cfops_antecipacao_pe import cfops_por_bucket
from lib.extrato_antecipado_pe import total_antecipacao_externa

ALIQ_INTERNA = Decimal("0.011")     # 1,1% — Antecipação dentro do estado
ADICIONAL_AQUISICOES = Decimal("0.35")  # 35% — adicional sobre a base do Crédito Presumido


def _to_decimal(v):
    return Decimal(str(v)) if v is not None else Decimal("0")


def _fmt(d: dict) -> dict:
    return {str(k): str(v) for k, v in d.items()}


# ============================================================================================
# IMPORTAÇÃO DA ROTINA 1024 (checkpoints_referencia, com valor_contabil) — específico da Apuração PE porque
# o ICMS Normal (app/lib/planilha.py:salvar_checkpoint_1024_bulk) não grava valor_contabil e trabalha numa
# competência de outro módulo ('icms_normal', não 'icms_antecipado').
# ============================================================================================

def salvar_checkpoint_1024_pe(session, competencia_id: int, linhas_1024: list[dict]) -> int:
    """`linhas_1024` é o retorno de app.lib.importar_1024.parse_rotina_1024 (lista de dicts com cfop,
    valor_contabil, valor_base, valor_icms). Substitui (apaga+insere) os checkpoints desta competência.

    Também apaga a confirmação de CFOPs de transferência já registrada (ver confirmar_transferencia_pe) —
    cada reimportação da Rotina 1024 obriga uma nova conferência, pra não deixar uma confirmação antiga
    "Sim" (ou "Não", pedindo correção) valendo pra um arquivo diferente do que gerou o aviso."""
    session.execute(text(
        "delete from checkpoints_referencia where competencia_id = :cid and fonte = 'rotina_1024'"
    ), {"cid": competencia_id})
    for r in linhas_1024:
        session.execute(text("""
            insert into checkpoints_referencia (competencia_id, fonte, cfop, valor_contabil, valor_base, valor_icms)
            values (:cid, 'rotina_1024', :cfop, :vc, :vb, :vi)
        """), {
            "cid": competencia_id, "cfop": int(r["cfop"]),
            "vc": float(r["valor_contabil"]), "vb": float(r["valor_base"]), "vi": float(r["valor_icms"]),
        })
    session.execute(text(
        "delete from confirmacoes_apuracao where competencia_id = :cid and tipo = 'cfop_transferencia_pe'"
    ), {"cid": competencia_id})
    session.commit()
    return len(linhas_1024)


# ============================================================================================
# CONFERÊNCIA DE CFOPs DE TRANSFERÊNCIA (pedido do usuário em 10/08/2026) — ao contrário do ICMS Normal
# (onde CFOP is_st+is_transferencia já é tratado automaticamente como sem efeito), o modelo de Crédito
# Presumido soma CFOP de transferência junto com os demais nas linhas normais (1.1 na Entrada; 2.1/2.3 na
# Saída) — um CFOP de transferência errado na Rotina 1024 pode distorcer a apuração sem nenhum aviso. Por
# isso, sempre que a Rotina 1024 importada tiver algum CFOP de transferência (is_transferencia=true), o
# analista precisa confirmar explicitamente que está correto antes de calcular a apuração.
# ============================================================================================

def listar_cfops_transferencia_checkpoint(session, competencia_id: int):
    """CFOPs de transferência (is_transferencia=true) presentes na Rotina 1024 importada desta competência
    — o que dispara o aviso de conferência. Lista vazia = nada a confirmar."""
    rows = session.execute(text("""
        select r.cfop, ce.descricao, r.valor_contabil,
               case when r.cfop / 1000 in (1, 2, 3) then 'Entrada' else 'Saída' end as direcao
        from checkpoints_referencia r
        join cfop_efetivo ce on ce.codigo = r.cfop
        where r.competencia_id = :cid and r.fonte = 'rotina_1024' and ce.is_transferencia = true
        order by r.cfop
    """), {"cid": competencia_id}).mappings().all()
    return list(rows)


def carregar_confirmacao(session, competencia_id: int, tipo: str):
    """Última confirmação registrada para este tipo (ver confirmacoes_apuracao) — None se ainda não houve
    nenhuma confirmação para esta competência/tipo (ex: depois de uma reimportação da Rotina 1024)."""
    row = session.execute(text("""
        select confirmado, observacao, confirmado_por_email, confirmado_em
        from confirmacoes_apuracao where competencia_id = :cid and tipo = :tipo
    """), {"cid": competencia_id, "tipo": tipo}).mappings().first()
    return dict(row) if row else None


def salvar_confirmacao(session, competencia_id: int, tipo: str, confirmado: bool, usuario: dict = None,
                        observacao: str = None) -> None:
    usuario = usuario or {}
    session.execute(text("""
        insert into confirmacoes_apuracao
            (competencia_id, tipo, confirmado, observacao, confirmado_por, confirmado_por_email, confirmado_em)
        values (:cid, :tipo, :confirmado, :obs, :por, :email, now())
        on conflict (competencia_id, tipo) do update
            set confirmado = excluded.confirmado, observacao = excluded.observacao,
                confirmado_por = excluded.confirmado_por, confirmado_por_email = excluded.confirmado_por_email,
                confirmado_em = excluded.confirmado_em
    """), {
        "cid": competencia_id, "tipo": tipo, "confirmado": confirmado, "obs": observacao,
        "por": usuario.get("id"), "email": usuario.get("email"),
    })
    session.commit()


def carregar_checkpoint_1024_pe(session, competencia_id: int):
    """DataFrame com os valores por CFOP importados da Rotina 1024 desta competência — pra conferência na
    tela (mesmo padrão do ICMS Normal)."""
    import pandas as pd
    rows = session.execute(text("""
        select r.cfop, c.descricao, r.valor_contabil, r.valor_base, r.valor_icms
        from checkpoints_referencia r
        left join cfop c on c.codigo = r.cfop
        where r.competencia_id = :cid and r.fonte = 'rotina_1024'
        order by r.cfop
    """), {"cid": competencia_id}).mappings().all()
    return pd.DataFrame(rows, columns=["cfop", "descricao", "valor_contabil", "valor_base", "valor_icms"])


# ============================================================================================
# ENTRADAS MANUAIS (override) — três linhas que podem ser digitadas na tela em vez de vir calculado:
# - "4.1.01": a única que NÃO tem fonte automática confiável (ver docstring do módulo).
# - "1.2"/"1.3": normalmente vêm encadeadas da competência anterior (ver competencia_anterior_id), mas o
#   analista pode sobrescrever — necessário, por exemplo, na primeira competência cadastrada no sistema
#   pra essa empresa (não tem "mês anterior" aqui dentro pra encadear, mas o valor recolhido existe de
#   verdade fora do sistema) ou pra corrigir um encadeamento que ficou errado. Pedido do usuário em
#   07/08/2026. Todas guardadas em checkpoints_referencia com fonte='manual_pe', linha=<código>,
#   valor_icms=valor digitado — mesmo padrão da fonte 'rotina_1025'.
# ============================================================================================

def carregar_valor_manual_pe(session, competencia_id: int, linha: str):
    v = session.execute(text("""
        select valor_icms from checkpoints_referencia
        where competencia_id = :cid and fonte = 'manual_pe' and linha = :linha
    """), {"cid": competencia_id, "linha": linha}).scalar()
    return _to_decimal(v) if v is not None else None


def salvar_valor_manual_pe(session, competencia_id: int, linha: str, valor) -> None:
    session.execute(text(
        "delete from checkpoints_referencia where competencia_id = :cid and fonte = 'manual_pe' and linha = :linha"
    ), {"cid": competencia_id, "linha": linha})
    session.execute(text("""
        insert into checkpoints_referencia (competencia_id, fonte, linha, valor_icms)
        values (:cid, 'manual_pe', :linha, :valor)
    """), {"cid": competencia_id, "linha": linha, "valor": float(valor)})
    session.commit()


def remover_valor_manual_pe(session, competencia_id: int, linha: str) -> None:
    """Apaga o override manual desta linha — volta a usar o valor calculado/encadeado automaticamente."""
    session.execute(text(
        "delete from checkpoints_referencia where competencia_id = :cid and fonte = 'manual_pe' and linha = :linha"
    ), {"cid": competencia_id, "linha": linha})
    session.commit()


# Aliases específicos da 4.1.01, mantidos porque já são usados na tela (app/pages/3_ICMS_PE.py).
def carregar_valor_4101_manual(session, competencia_id: int):
    return carregar_valor_manual_pe(session, competencia_id, "4.1.01")


def salvar_valor_4101_manual(session, competencia_id: int, valor) -> None:
    salvar_valor_manual_pe(session, competencia_id, "4.1.01", valor)


# ============================================================================================
# MOTOR DE CÁLCULO
# ============================================================================================

def _cfops_presentes(session, competencia_id: int, faixa) -> dict:
    """{cfop: {valor_contabil, valor_base, valor_icms, descricao, is_st, is_transferencia}} — todo CFOP da
    Rotina 1024 desta competência dentro da faixa (faixa = (1,2,3) pra Entrada, (5,6,7) pra Saída)."""
    rows = session.execute(text("""
        select r.cfop, r.valor_contabil, r.valor_base, r.valor_icms,
               ce.descricao, ce.is_st, ce.is_transferencia
        from checkpoints_referencia r
        join cfop_efetivo ce on ce.codigo = r.cfop
        where r.competencia_id = :cid and r.fonte = 'rotina_1024'
    """), {"cid": competencia_id}).mappings().all()
    return {
        r["cfop"]: {
            "valor_contabil": _to_decimal(r["valor_contabil"]),
            "valor_base": _to_decimal(r["valor_base"]),
            "valor_icms": _to_decimal(r["valor_icms"]),
            "descricao": r["descricao"],
            "is_st": bool(r["is_st"]),
            "is_transferencia": bool(r["is_transferencia"]),
        }
        for r in rows if (r["cfop"] // 1000) in faixa
    }


def competencia_anterior_id(session, empresa_id: int, ano: int, mes: int, modulo: str = "icms_antecipado"):
    """Id da competência imediatamente anterior (mesma empresa/módulo) que já tem apuração calculada —
    usada pra encadear as linhas 1.2/1.3 (créditos de antecipação do mês anterior) e 6 (saldo credor
    anterior). Retorna None se não existir (primeira competência cadastrada no sistema)."""
    ano_ant, mes_ant = (ano - 1, 12) if mes == 1 else (ano, mes - 1)
    return session.execute(text("""
        select c.id from competencias c
        where c.empresa_id = :eid and c.modulo = :modulo and c.ano = :ano and c.mes = :mes
          and exists (select 1 from apuracao_linhas a where a.competencia_id = c.id)
    """), {"eid": empresa_id, "modulo": modulo, "ano": ano_ant, "mes": mes_ant}).scalar()


def _valor_linha(session, competencia_id, linha) -> Decimal:
    v = session.execute(text(
        "select valor from apuracao_linhas where competencia_id = :cid and linha = :linha"
    ), {"cid": competencia_id, "linha": linha}).scalar()
    return _to_decimal(v)


def sugerir_valor_4101(session, competencia_id: int) -> Decimal:
    """Sugestão calculada pra linha 4.1.01 (Valor Total das Saídas ajustado): total de Valores Contábeis
    das Saídas, menos "outras saídas" (CFOP com descrição "OUTRA SA..." — ex: 5949) e menos as CFOPs de
    devolução de compra (descrição "DEV...", ex: 6202/5202).

    IMPORTANTE — essa sugestão NÃO é a fórmula completa: no único mês conferido (06/2026, Ultra Comércio),
    o valor real da planilha ficou R$ 30.828,00 ABAIXO desta sugestão, por causa de uma reclassificação
    contábil que não aparece em nenhuma CFOP do Rotina 1024 (rótulo da linha na planilha original é "...
    outras saídas, devoluções E RECLASSIFICAÇÃO" — a reclassificação é a parte que falta aqui). Por isso
    esta linha é editável manualmente na tela, com este valor só como ponto de partida — o analista deve
    conferir/ajustar contra a apuração anterior ou outro controle interno da reclassificação."""
    saida = _cfops_presentes(session, competencia_id, (5, 6, 7))
    total_vc = sum((d["valor_contabil"] for d in saida.values()), Decimal("0"))
    outras_saida = sum(
        (d["valor_contabil"] for d in saida.values() if (d["descricao"] or "").upper().startswith("OUTRA SA")),
        Decimal("0"),
    )
    devolucao_saida = sum(
        (d["valor_contabil"] for d in saida.values() if (d["descricao"] or "").upper().startswith("DEV")),
        Decimal("0"),
    )
    return total_vc - outras_saida - devolucao_saida


def calcular_apuracao_pe(session, competencia_id: int, empresa_id: int, ano: int, mes: int,
                          valor_4101_manual=None, valor_1_2_manual=None, valor_1_3_manual=None) -> list[LinhaApuracao]:
    """Calcula todas as linhas da Apuração ICMS PE (Crédito Presumido) para uma competência. Não grava no
    banco — quem chama decide se persiste (ver salvar_apuracao, reaproveitado de calculo_icms_normal).

    `valor_4101_manual`: valor digitado pelo analista pra linha 4.1.01 (ver sugerir_valor_4101). Se None,
    tenta carregar o que já foi salvo (carregar_valor_4101_manual); se também não houver, usa a sugestão
    calculada (sugerir_valor_4101) — nesse caso o resultado da linha 4. fica sujeito à imprecisão descrita
    lá.

    `valor_1_2_manual`/`valor_1_3_manual`: override manual das linhas 1.2/1.3 (créditos de antecipação do
    mês anterior — ver competencia_anterior_id). Por padrão essas linhas são encadeadas automaticamente da
    competência anterior já calculada no sistema; passe um valor aqui (ou salve com salvar_valor_manual_pe)
    pra sobrescrever — necessário, por exemplo, na primeira competência cadastrada pra essa empresa, onde
    não existe "mês anterior" dentro do sistema pra encadear.

    Linha 6 (Saldo Crédito Anterior) — regra de semestre (pedido do usuário em 10/08/2026, corrigido no
    mesmo dia: é semestral, não trimestral): pelo Decreto de Crédito Presumido de PE, saldo credor não pode
    ser transportado de um semestre pro outro, tem que ser zerado com um lançamento de "Outros Débitos" na
    própria Rotina 1025/e-Fisco no fechamento de junho e dezembro. Por isso o encadeamento automático da
    linha 6 NUNCA atravessa essa fronteira: se a competência anterior era junho ou dezembro, a linha 6
    desta competência começa zerada mesmo que aquela tenha fechado credora. E se ESTA competência é junho
    ou dezembro e fecha credora, a linha 7 vem com um aviso
    (`detalhe["aviso_zerar_saldo_credor_semestre"]`) pra lembrar o analista de fazer esse lançamento antes
    de fechar o período — ver o aviso também na tela (aba Apuração)."""
    entrada = _cfops_presentes(session, competencia_id, (1, 2, 3))
    saida = _cfops_presentes(session, competencia_id, (5, 6, 7))
    buckets = cfops_por_bucket(session, empresa_id)  # {"interna": [cfop,...], "externa": [cfop,...]}

    # --- 3.1 / 3.1.1 — Antecipação dentro do estado (1,1% sobre a base "interna" cadastrada) ---
    det_311 = {c: entrada[c]["valor_contabil"] for c in buckets["interna"] if c in entrada}
    base_interna = sum(det_311.values(), Decimal("0"))
    linha_3_1 = base_interna * ALIQ_INTERNA

    # --- 3.2 / 3.2.1 — Antecipação fora do estado (base cadastrada só para referência/auditoria; o VALOR
    #     da linha vem do Extrato e-Fisco, não de base × alíquota — ver docstring de extrato_antecipado_pe.py)
    det_321 = {c: entrada[c]["valor_contabil"] if c in entrada else saida[c]["valor_contabil"]
               for c in buckets["externa"] if c in entrada or c in saida}
    base_externa = sum(det_321.values(), Decimal("0"))
    linha_3_2 = total_antecipacao_externa(session, competencia_id)

    linha_3 = linha_3_1 + linha_3_2

    # --- 1. Créditos Totais ---
    total_credito_entrada = sum((d["valor_icms"] for d in entrada.values()), Decimal("0"))
    dev_entrada_nao_st = {c: d["valor_icms"] for c, d in entrada.items()
                           if (d["descricao"] or "").upper().startswith("DEV") and not d["is_st"]}
    dev_entrada_st = {c: d["valor_icms"] for c, d in entrada.items()
                       if (d["descricao"] or "").upper().startswith("DEV") and d["is_st"]}
    linha_1_4 = sum(dev_entrada_nao_st.values(), Decimal("0"))   # "Devoluções de vendas 1202/2202"
    linha_1_5 = sum(dev_entrada_st.values(), Decimal("0"))       # "Devoluções de vendas ST (Outros)"
    linha_1_1 = total_credito_entrada - linha_1_4 - linha_1_5    # "Créditos Entradas - Devoluções"

    comp_ant_id = competencia_anterior_id(session, empresa_id, ano, mes)

    if valor_1_2_manual is None:
        valor_1_2_manual = carregar_valor_manual_pe(session, competencia_id, "1.2")
    if valor_1_2_manual is not None:
        linha_1_2, origem_1_2 = _to_decimal(valor_1_2_manual), "manual"
    else:
        linha_1_2 = _valor_linha(session, comp_ant_id, "3.1") if comp_ant_id else Decimal("0")
        origem_1_2 = "encadeado_competencia_anterior" if comp_ant_id else "sem_competencia_anterior"

    if valor_1_3_manual is None:
        valor_1_3_manual = carregar_valor_manual_pe(session, competencia_id, "1.3")
    if valor_1_3_manual is not None:
        linha_1_3, origem_1_3 = _to_decimal(valor_1_3_manual), "manual"
    else:
        linha_1_3 = _valor_linha(session, comp_ant_id, "3.2") if comp_ant_id else Decimal("0")
        origem_1_3 = "encadeado_competencia_anterior" if comp_ant_id else "sem_competencia_anterior"

    linha_1 = linha_1_1 + linha_1_2 + linha_1_3 + linha_1_4 + linha_1_5

    # --- 2. Débitos Totais ---
    total_debito_saida = sum((d["valor_icms"] for d in saida.values()), Decimal("0"))
    dev_saida = {c: d["valor_icms"] for c, d in saida.items() if (d["descricao"] or "").upper().startswith("DEV")}
    transf_saida = {c: d["valor_icms"] for c, d in saida.items()
                     if d["is_transferencia"] and c not in dev_saida}
    linha_2_2 = sum(dev_saida.values(), Decimal("0"))       # "Estorno de crédito - Devolução de compras"
    linha_2_3 = sum(transf_saida.values(), Decimal("0"))    # "Débito Transferências"
    linha_2_1 = total_debito_saida - linha_2_2 - linha_2_3  # "Débito Saídas"

    linha_2 = linha_2_1 + linha_2_2 + linha_2_3

    # --- 4. Crédito Presumido ---
    if valor_4101_manual is None:
        valor_4101_manual = carregar_valor_4101_manual(session, competencia_id)
    if valor_4101_manual is None:
        valor_4101_manual = sugerir_valor_4101(session, competencia_id)
        origem_4101 = "sugestao_calculada"
    else:
        origem_4101 = "manual"
    linha_4_1_01 = _to_decimal(valor_4101_manual)
    linha_4_1_02 = linha_2  # "Valor Total dos débitos" = linha 2 (Débitos Totais)
    linha_4_1 = (linha_4_1_02 / linha_4_1_01) if linha_4_1_01 else Decimal("0")  # Alíquota Média

    det_saida_dev_vc = {c: saida[c]["valor_contabil"] for c in dev_saida}
    linha_4_2_01 = base_interna + base_externa - sum(det_saida_dev_vc.values(), Decimal("0"))
    linha_4_2_02 = linha_4_2_01 * ADICIONAL_AQUISICOES
    linha_4_2 = linha_4_2_01 + linha_4_2_02

    linha_4_3_01 = linha_3          # "Antecipação" (= 3.1 + 3.2)
    linha_4_3_02 = linha_1_1        # "Crédito Entradas" (= 1.1)
    linha_4_3 = linha_4_3_01 + linha_4_3_02

    linha_4 = linha_4_1 * linha_4_2 - linha_4_3

    # --- 5/6/7 — Valor a Recolher, Saldo Anterior, Valor Recolher Atual ---
    linha_5 = linha_2 - linha_1 - linha_4
    linha_7_anterior = _valor_linha(session, comp_ant_id, "7") if comp_ant_id else Decimal("0")

    # Regra do Decreto de Crédito Presumido de PE (pedido do usuário em 10/08/2026, corrigido no mesmo dia:
    # é SEMESTRAL — meses 06 e 12 — não trimestral): saldo credor NÃO pode ser transportado de um semestre
    # pro outro — tem que ser zerado com um lançamento de "Outros Débitos" (linha 02) direto na Rotina
    # 1025/e-Fisco, sempre no fechamento de junho e dezembro. Por isso, se a competência ANTERIOR era junho
    # ou dezembro, o encadeamento automático da linha 6 pára aí — mesmo que ela tenha fechado credora, não
    # carrega esse saldo pra cá (esse saldo já deveria ter sido zerado no e-Fisco antes de fechar aquele
    # semestre; ver aviso na tela de Apuração quando ESTA competência for de fechamento de semestre e
    # fechar credora).
    mes_anterior = 12 if mes == 1 else mes - 1
    zerado_por_semestre = mes_anterior in (6, 12)
    if zerado_por_semestre:
        linha_6 = Decimal("0")
        origem_6 = "zerado_fechamento_semestre"
    else:
        # só carrega saldo pra frente se a competência anterior fechou credora (valor negativo = "a
        # recolher" negativo, ou seja, saldo credor); se fechou devedora (>=0, já recolhida), não há saldo
        # a transportar.
        linha_6 = linha_7_anterior if linha_7_anterior < 0 else Decimal("0")
        origem_6 = "encadeado_competencia_anterior" if comp_ant_id else "sem_competencia_anterior"
    linha_7 = linha_6 + linha_5
    fecha_semestre = mes in (6, 12)

    linhas = [
        LinhaApuracao("1", "Créditos Totais", linha_1),
        LinhaApuracao("1.1", "Créditos Entradas - Devoluções", linha_1_1,
                       {"total_credito_entrada": str(total_credito_entrada),
                        "menos_devolucao_nao_st": _fmt(dev_entrada_nao_st),
                        "menos_devolucao_st": _fmt(dev_entrada_st)}),
        LinhaApuracao("1.2", "Crédito 1,1% recolhido no mês anterior", linha_1_2,
                       {"origem": origem_1_2, "competencia_anterior_id": comp_ant_id}),
        LinhaApuracao("1.3", "Crédito 6% recolhido no mês anterior", linha_1_3,
                       {"origem": origem_1_3, "competencia_anterior_id": comp_ant_id}),
        LinhaApuracao("1.4", "Estorno de débitos - Devoluções de vendas (não-ST)", linha_1_4, _fmt(dev_entrada_nao_st)),
        LinhaApuracao("1.5", "Estorno de débitos - Devoluções de vendas ST (Outros)", linha_1_5, _fmt(dev_entrada_st)),

        LinhaApuracao("2", "Débitos Totais", linha_2),
        LinhaApuracao("2.1", "Débito Saídas", linha_2_1, {"total_debito_saida": str(total_debito_saida)}),
        LinhaApuracao("2.2", "Estorno de crédito - Devolução de compras", linha_2_2, _fmt(dev_saida)),
        LinhaApuracao("2.3", "Débito Transferências", linha_2_3, _fmt(transf_saida)),

        LinhaApuracao("3", "Antecipação", linha_3),
        LinhaApuracao("3.1", "Antecipação 1,1% dentro do estado", linha_3_1, {"base": str(base_interna)}),
        LinhaApuracao("3.1.1", "Total base Entrada/Antecipação interna", base_interna, _fmt(det_311)),
        LinhaApuracao("3.2", "Antecipação fora do estado (Extrato e-Fisco)", linha_3_2),
        LinhaApuracao("3.2.1", "Total base Entrada externa (referência/auditoria)", base_externa, _fmt(det_321)),

        LinhaApuracao("4", "Crédito Presumido (4.1 x 4.2 - 4.3)", linha_4),
        LinhaApuracao("4.1", "Alíquota Média (4.1.02/4.1.01)", linha_4_1,
                       {"origem_4.1.01": origem_4101}),
        LinhaApuracao("4.1.01", "Valor Total das Saídas ajustado (outras saídas, devoluções e reclassificação)",
                       linha_4_1_01, {"origem": origem_4101, "sugestao_calculada": str(sugerir_valor_4101(session, competencia_id))}),
        LinhaApuracao("4.1.02", "Valor Total dos débitos", linha_4_1_02),
        LinhaApuracao("4.2", "Base de cálculo Crédito Presumido", linha_4_2),
        LinhaApuracao("4.2.01", "Aquisições de Mercadorias - Devoluções - Serviços - Remessas", linha_4_2_01,
                       {"base_interna": str(base_interna), "base_externa": str(base_externa),
                        "menos_devolucao_compra_vc": _fmt(det_saida_dev_vc)}),
        LinhaApuracao("4.2.02", "Adicional 35% Aquisições", linha_4_2_02),
        LinhaApuracao("4.3", "Deduções Demais Créditos", linha_4_3),
        LinhaApuracao("4.3.01", "Antecipação", linha_4_3_01),
        LinhaApuracao("4.3.02", "Crédito Entradas", linha_4_3_02),

        LinhaApuracao("5", "Valor a Recolher (2. - 1. - 4.)", linha_5),
        LinhaApuracao("6", "Saldo Crédito Anterior", linha_6,
                       {"origem": origem_6, "competencia_anterior_id": comp_ant_id,
                        "valor_linha_7_competencia_anterior": str(linha_7_anterior)}),
        LinhaApuracao("7", "Valor Recolher Atual (5. - 6.)", linha_7,
                       {"fecha_semestre": fecha_semestre,
                        "aviso_zerar_saldo_credor_semestre":
                            ("Este é o último mês do semestre (junho ou dezembro) e a apuração fechou "
                             "credora — pelo Decreto de Crédito Presumido de PE, esse saldo NÃO pode ser "
                             "transportado para o próximo semestre: precisa ser zerado com um lançamento "
                             "de \"Outros Débitos\" (linha 02) direto na Rotina 1025/e-Fisco, no "
                             "fechamento deste período.")
                            if (fecha_semestre and linha_7 < 0) else None}),
    ]
    return linhas


# ============================================================================================
# CONFERÊNCIA COM A ROTINA 1025 (Livro Registro de Apuração do ICMS) — pedido do usuário em 10/08/2026.
#
# A Apuração ICMS PE usa linhas próprias (1, 1.1, 1.2... 7), diferentes das linhas oficiais do livro
# (01 a 14, as mesmas que o ICMS Normal usa 1 pra 1 — ver comparar_com_checkpoint_1025 em
# calculo_icms_normal.py). Aqui é preciso MAPEAR as linhas da Apuração PE para as linhas do livro antes de
# comparar. Mapeamento validado ao centavo em 10/08/2026 contra o PDF real da Rotina 1025 da Ultra Comércio,
# competência 05/2026 (fornecido pelo usuário):
#
#   livro 04 (Subtotal Débito)              = PE 2       (Débitos Totais)
#   livro 05 (Entradas com crédito)         = PE 1.1     (Créditos Entradas - Devoluções)
#   livro 06 (Outros Créditos)              = PE 1.2 + 1.3 + 4   (antecipação do mês anterior + crédito
#                                              presumido calculado — confirmado pelo usuário: "o credito de
#                                              1,1% e 6% do periodo anterior entram na apuração como
#                                              credito, além disso o credito presumido calculado entra na
#                                              apuração" — no PDF de 05/2026 essas 3 parcelas, 5.912,93 +
#                                              36.054,83 + 127.462,01, somam exatamente os 169.429,77 do
#                                              "Subtotal outros Créditos")
#   livro 08 (Subtotal Crédito)             = PE 1 + 4   (Créditos Totais + Crédito Presumido)
#   livro 09 (Saldo Credor Período Anterior) = -PE 6     (no livro aparece positivo; no nosso modelo a
#                                              linha 6 já é guardada negativa quando há saldo credor)
#   livro 10 (Total)                        = livro 08 + livro 09
#   livro 11/13 (Saldo devedor / Imposto a Recolher) = PE 7 quando PE 7 >= 0 (senão 0)
#   livro 14 (Saldo Credor a Transportar)   = -PE 7 quando PE 7 < 0 (senão 0)
#
# As linhas 01/02/03/07/12 do livro não têm um mapeamento confiável no modelo de Crédito Presumido (não há,
# por exemplo, um "Estorno de Créditos" nem "Outros Débitos" discriminado separadamente na Apuração PE) —
# aparecem na comparação só como referência (valor do PDF), sem comparação automática.
# ============================================================================================

_LIVRO_1025_LABELS = {
    "01": "Por Saídas/Prestações com débito do imposto",
    "02": "Outros Débitos",
    "03": "Estorno de Créditos",
    "04": "Subtotal Débito",
    "05": "Por Entradas/Aquisições com crédito do imposto",
    "06": "Outros Créditos",
    "07": "Estorno de Débitos",
    "08": "Subtotal Crédito",
    "09": "Saldo Credor do Período Anterior",
    "10": "Total",
    "11": "Saldo Devedor (Débito Menos Crédito)",
    "12": "Deduções",
    "13": "Imposto a Recolher",
    "14": "Saldo Credor a Transportar",
}


def comparar_com_checkpoint_1025_pe(session, competencia_id: int) -> list[dict]:
    """Compara a Apuração ICMS PE já calculada (apuracao_linhas) contra os valores do livro oficial (Rotina
    1025) importados/digitados em checkpoints_referencia (fonte='rotina_1025') — ver mapeamento na docstring
    acima. Devolve uma linha por código do livro (01 a 14), com valor_calc=None para as linhas sem
    mapeamento confiável neste regime. Lista vazia se a apuração ainda não foi calculada."""
    linhas_calc = {r["linha"]: _to_decimal(r["valor"]) for r in session.execute(text(
        "select linha, valor from apuracao_linhas where competencia_id = :cid"
    ), {"cid": competencia_id}).mappings().all()}
    if not linhas_calc:
        return []

    linhas_ref = {r["linha"]: _to_decimal(r["valor_icms"]) for r in session.execute(text(
        "select linha, valor_icms from checkpoints_referencia "
        "where competencia_id = :cid and fonte = 'rotina_1025'"
    ), {"cid": competencia_id}).mappings().all()}

    def g(cod):
        return linhas_calc.get(cod, Decimal("0"))

    saldo_7 = g("7")
    calc = {
        "04": g("2"),
        "05": g("1.1"),
        "06": g("1.2") + g("1.3") + g("4"),
        "08": g("1") + g("4"),
        "09": -g("6"),  # linha 6 já é <= 0 no nosso modelo (0 ou saldo credor negativo)
    }
    calc["10"] = calc["08"] + calc["09"]
    if saldo_7 >= 0:
        calc["11"] = calc["13"] = saldo_7
        calc["14"] = Decimal("0")
    else:
        calc["11"] = calc["13"] = Decimal("0")
        calc["14"] = -saldo_7

    resultado = []
    for cod, label in _LIVRO_1025_LABELS.items():
        valor_ref = linhas_ref.get(cod)
        valor_calc = calc.get(cod)
        diff = (valor_calc - valor_ref) if (valor_calc is not None and valor_ref is not None) else None
        resultado.append({
            "linha": cod, "descricao": label, "valor_calc": valor_calc, "valor_ref": valor_ref, "diff": diff,
            "mapeado": valor_calc is not None,
        })
    return resultado
