"""
Cálculo da apuração ICMS Normal (linhas 01-14 do livro), a partir dos itens de NF importados +
lançamentos manuais (DIFAL, CIAP, DAE Antecipado, ajustes de CFOP não importados).

Regras (documentadas em detalhe em claude/metodologia-icms-normal.md no projeto, validadas contra os
dados reais de julho/2026 da Sodine Atacado F3):

- Um item de Saída/Entrada é classificado por CFOP via a tabela `cfop` (view `cfop_efetivo`, que já aplica
  o ajuste manual quando existe).
- CFOP is_st=True e is_transferencia=True (ex: 1409, 5409): mercadoria já sujeita a ST movida entre
  empresas do grupo. Não gera crédito nem débito novo (ICMS-ST já foi retido/pago na origem) — fica fora
  de todas as linhas normais.
- CFOP is_st=True e is_transferencia=False (venda/compra normal sujeita a ST, ex: 1403, 5403): gera
  crédito/débito "bruto" (Base × Alíquota) que É integralmente estornado (linha 03 na Entrada, linha 07 na
  Saída) — efeito líquido zero, mas os dois lados (bruto e estorno) aparecem no livro.
- CFOP is_st=False (compra/venda normal, ex: 1102, 5102): gera crédito/débito pleno, sem estorno.

CORREÇÃO (06/08/2026): até aqui, TODO débito de saída (ST ou não) caía direto na "02 - Outros Débitos", e a
"01 - Por Saídas com débito" ficava sempre zerada — essa regra tinha sido tirada de uma leitura do PDF do
Livro Registro de Apuração real da Sodine, mas a leitura estava errada: o valor que parecia estar alinhado
com a linha "02" era, na verdade, da linha "01" (o PDF quebra a linha por causa da descrição longa de "01",
o que engana visualmente). Corrigido: agora TODO débito de saída (bruto, ST ou não) vai para a "01 - Por
Saídas com débito" (a parte ST continua sendo estornada na "07", igual antes, só mudou de qual linha ela
"sai"). A "02 - Outros Débitos" agora é só a soma dos lançamentos manuais de débito (aba Ajustes na
Apuração, tipo "ajuste_cfop_debito") — não recebe mais nada calculado automaticamente dos itens de NF.
Não muda o total final (linha 04 = 01+02+03 continua a mesma soma), só corrige em qual linha específica
cada valor aparece, pra bater exatamente com o livro oficial linha a linha.
"""
from dataclasses import dataclass, field
from decimal import Decimal
from sqlalchemy import text


@dataclass
class LinhaApuracao:
    linha: str
    descricao: str
    valor: Decimal
    detalhe: dict = field(default_factory=dict)


def _to_decimal(v):
    return Decimal(str(v)) if v is not None else Decimal("0")


def calcular_apuracao_icms_normal(session, competencia_id: int) -> list[LinhaApuracao]:
    """Calcula as linhas 01-14 do livro de apuração para uma competência e retorna a lista de linhas.
    Não grava no banco — quem chama decide se persiste (ver salvar_apuracao)."""

    itens = session.execute(text("""
        select ni.tipo_operacao, ni.cfop, ni.ncm, ni.base_icms, ni.valor_icms,
               ce.is_st, ce.is_transferencia
        from notas_fiscais_itens ni
        join cfop_efetivo ce on ce.codigo = ni.cfop
        where ni.competencia_id = :cid
    """), {"cid": competencia_id}).mappings().all()

    lancamentos = session.execute(text("""
        select tipo, cfop_relacionado, valor, descricao
        from lancamentos_manuais
        where competencia_id = :cid
    """), {"cid": competencia_id}).mappings().all()

    # --- acumuladores por linha, com detalhe por CFOP para auditoria ---
    debito_saidas = Decimal("0")              # 01  (TODO débito de saída, bruto — ST ou não)
    estorno_creditos = Decimal("0")           # 03  (entrada, ST não-transferência)
    entradas_com_credito = Decimal("0")       # 05  (entrada, não-ST)
    estorno_debitos = Decimal("0")            # 07  (saída, ST não-transferência — estorna parte da 01)

    det_01, det_03, det_05, det_07 = {}, {}, {}, {}

    for it in itens:
        icms = _to_decimal(it["valor_icms"])
        cfop = it["cfop"]
        is_st = bool(it["is_st"])
        is_transf = bool(it["is_transferencia"])

        if is_st and is_transf:
            # Ex: 1409 / 5409 — transferência de mercadoria já ST entre empresas do grupo. Sem efeito.
            continue

        if it["tipo_operacao"] == "saida":
            # débito bruto de TODA saída (ST ou não) vai para "01 - Por Saídas com débito" (ver docstring
            # do módulo — correção de 06/08/2026, antes ia pra "02" por engano)
            debito_saidas += icms
            det_01[cfop] = det_01.get(cfop, Decimal("0")) + icms
            if is_st:
                estorno_debitos += icms
                det_07[cfop] = det_07.get(cfop, Decimal("0")) + icms
        else:  # entrada
            if is_st:
                estorno_creditos += icms
                det_03[cfop] = det_03.get(cfop, Decimal("0")) + icms
                # crédito bruto do item ST também soma em "05" — é estornado integralmente em "03"
                entradas_com_credito += icms
                det_05[cfop] = det_05.get(cfop, Decimal("0")) + icms
            else:
                entradas_com_credito += icms
                det_05[cfop] = det_05.get(cfop, Decimal("0")) + icms

    # --- lançamentos manuais ---
    difal = sum((_to_decimal(l["valor"]) for l in lancamentos if l["tipo"] == "difal_debito"), Decimal("0"))
    outros_creditos = sum(
        (_to_decimal(l["valor"]) for l in lancamentos
         if l["tipo"] in ("ciap_credito", "dae_antecipado_credito")),
        Decimal("0"),
    )
    det_06 = {l["descricao"]: _to_decimal(l["valor"]) for l in lancamentos
              if l["tipo"] in ("ciap_credito", "dae_antecipado_credito")}

    ajuste_cfop_credito = sum(
        (_to_decimal(l["valor"]) for l in lancamentos if l["tipo"] == "ajuste_cfop_credito"), Decimal("0")
    )
    # "02 - Outros Débitos" agora é só isto: a soma dos lançamentos manuais de débito (ver docstring do
    # módulo, correção de 06/08/2026) — não recebe mais nada calculado automaticamente dos itens de NF.
    outros_debitos = sum(
        (_to_decimal(l["valor"]) for l in lancamentos if l["tipo"] == "ajuste_cfop_debito"), Decimal("0")
    )
    det_02 = {l["descricao"]: _to_decimal(l["valor"]) for l in lancamentos if l["tipo"] == "ajuste_cfop_debito"}
    entradas_com_credito += ajuste_cfop_credito

    # --- linhas do livro ---
    linha_01 = debito_saidas
    linha_02 = outros_debitos
    linha_03 = estorno_creditos
    linha_04 = linha_01 + linha_02 + linha_03           # subtotal débito
    linha_05 = entradas_com_credito
    linha_06 = outros_creditos
    linha_07 = estorno_debitos
    linha_08 = linha_05 + linha_06 + linha_07           # subtotal crédito
    linha_09 = Decimal("0")                              # saldo credor do período anterior — TODO: encadear
    saldo = linha_08 + linha_09 - linha_04
    if saldo >= 0:
        linha_11 = Decimal("0")  # saldo devedor (débito > crédito) -> aqui é o inverso, ver nota abaixo
        linha_13 = Decimal("0")
        linha_14 = saldo         # saldo credor a transportar
    else:
        linha_11 = -saldo
        linha_13 = -saldo        # imposto a recolher = saldo devedor (sem deduções, linha 12 = 0 por ora)
        linha_14 = Decimal("0")

    linhas = [
        LinhaApuracao("01", "Por Saídas com débito", linha_01, {"por_cfop": det_01}),
        LinhaApuracao("02", "Outros Débitos", linha_02, {"lancamentos_manuais": {k: str(v) for k, v in det_02.items()}}),
        LinhaApuracao("03", "Estorno de Créditos", linha_03, {"por_cfop": det_03}),
        LinhaApuracao("04", "Subtotal Débito", linha_04),
        LinhaApuracao("05", "Por Entradas com crédito", linha_05, {"por_cfop": det_05}),
        LinhaApuracao("06", "Outros Créditos", linha_06, {"lancamentos": {k: str(v) for k, v in det_06.items()}}),
        LinhaApuracao("07", "Estorno de Débitos", linha_07, {"por_cfop": det_07}),
        LinhaApuracao("08", "Subtotal Crédito", linha_08),
        LinhaApuracao("09", "Saldo Credor do Período Anterior", linha_09),
        LinhaApuracao("11", "Saldo Devedor (Débito menos Crédito)", linha_11),
        LinhaApuracao("12", "Deduções", Decimal("0")),
        LinhaApuracao("13", "Imposto a Recolher", linha_13),
        LinhaApuracao("14", "Saldo Credor a Transportar", linha_14),
    ]
    return linhas


def salvar_apuracao(session, competencia_id: int, linhas: list[LinhaApuracao]):
    import json
    for l in linhas:
        session.execute(text("""
            insert into apuracao_linhas (competencia_id, linha, descricao, valor, detalhe, calculado_em)
            values (:cid, :linha, :descricao, :valor, :detalhe, now())
            on conflict (competencia_id, linha) do update
                set descricao = excluded.descricao,
                    valor = excluded.valor,
                    detalhe = excluded.detalhe,
                    calculado_em = now()
        """), {
            "cid": competencia_id, "linha": l.linha, "descricao": l.descricao,
            "valor": str(l.valor), "detalhe": json.dumps(l.detalhe, default=str),
        })
    session.commit()


def comparar_com_checkpoint_1024(session, competencia_id: int) -> list[dict]:
    """Compara soma por CFOP (Entrada+Saída) calculada dos relatórios de NF contra os valores da Rotina
    1024 digitados manualmente em checkpoints_referencia. Retorna lista de divergências (vazia = tudo bate).
    """
    rows = session.execute(text("""
        with calc as (
            select cfop, sum(base_icms) as base_calc, sum(valor_icms) as icms_calc
            from notas_fiscais_itens
            where competencia_id = :cid
            group by cfop
        )
        select r.cfop, r.valor_base as base_ref, r.valor_icms as icms_ref,
               coalesce(c.base_calc, 0) as base_calc, coalesce(c.icms_calc, 0) as icms_calc
        from checkpoints_referencia r
        left join calc c on c.cfop = r.cfop
        where r.competencia_id = :cid and r.fonte = 'rotina_1024'
    """), {"cid": competencia_id}).mappings().all()

    divergencias = []
    for r in rows:
        diff_base = _to_decimal(r["base_calc"]) - _to_decimal(r["base_ref"])
        diff_icms = _to_decimal(r["icms_calc"]) - _to_decimal(r["icms_ref"])
        if abs(diff_base) > Decimal("0.05") or abs(diff_icms) > Decimal("0.05"):
            divergencias.append({
                "cfop": r["cfop"], "diff_base": diff_base, "diff_icms": diff_icms,
                "base_ref": r["base_ref"], "base_calc": r["base_calc"],
                "icms_ref": r["icms_ref"], "icms_calc": r["icms_calc"],
            })
    return divergencias


def comparar_com_checkpoint_1025(session, competencia_id: int) -> list[dict]:
    """Compara as linhas 01-14 calculadas (apuracao_linhas) contra a Rotina 1025 digitada manualmente."""
    rows = session.execute(text("""
        select a.linha, a.valor as valor_calc, r.valor_icms as valor_ref
        from apuracao_linhas a
        left join checkpoints_referencia r
            on r.competencia_id = a.competencia_id and r.fonte = 'rotina_1025' and r.linha = a.linha
        where a.competencia_id = :cid
        order by a.linha
    """), {"cid": competencia_id}).mappings().all()

    divergencias = []
    for r in rows:
        if r["valor_ref"] is None:
            continue
        diff = _to_decimal(r["valor_calc"]) - _to_decimal(r["valor_ref"])
        if abs(diff) > Decimal("0.05"):
            divergencias.append({
                "linha": r["linha"], "valor_calc": r["valor_calc"],
                "valor_ref": r["valor_ref"], "diff": diff,
            })
    return divergencias
