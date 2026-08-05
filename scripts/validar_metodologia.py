"""
Script de validação: roda a MESMA lógica de app/lib/calculo_icms_normal.py (reimplementada aqui em
pandas puro, sem precisar de banco) contra os relatórios de Entrada/Saída reais de uma competência, e
compara o resultado contra os valores oficiais da Rotina 1025 (você digita os valores de referência).

Rodado contra julho/2026 (Sodine Atacado F3) em 05/08/2026, bateu exato (diferença R$ 0,00) no "13 -
Imposto a Recolher" (R$ 6.921,47) depois de incluir o lançamento manual do CFOP 1602 (R$ 3.814,87 —
esse CFOP é lançado direto no sistema contábil e não aparece no relatório de Entrada). As linhas 03/04/05/08
ainda mostram um resíduo de R$ 283,00 vindo da diferença não resolvida dos CFOPs 1403/1411 (ver
claude/metodologia-icms-normal.md no projeto) — mas esse resíduo se cancela sozinho no saldo final porque
aparece igualmente do lado do crédito bruto (05) e do estorno (03).

Uso:
    python scripts/validar_metodologia.py \
        --entrada RELATORIO_ENTRADA.xls --saida RELATORIO_SAIDA.xls \
        --ciap 161.73 --dae 710.06 --dae 72.76 \
        --ajuste-cfop 1602:3814.87 \
        --referencia-imposto-a-recolher 6921.47
"""
import argparse
import sys
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

COLS_ENTRADA = [
    "Fornecedor", "NF", "TipoGenero", "DtEmissao", "DtEntrada", "Produto", "NCM", "CFOP",
    "ValorProduto", "Col10", "Col11", "Col12", "Col13", "AliqFCP", "ValorFCP", "AliqICMS",
    "BaseICMS", "ValorICMS", "Col19", "Col20", "ValorTotal", "UF", "Prazo",
]
COLS_SAIDA = [
    "Cliente", "NF", "TipoItem", "DtEmissao", "Produto", "NCM", "CFOP", "ValorProduto",
    "Col9", "Col10", "Col11", "Col12", "Col13", "Col14", "AliqICMS", "BaseICMS", "ValorICMS",
    "Col18", "Col19", "ValorTotal", "UF", "Prazo",
]

# Mesmos ajustes confirmados em scripts/seed_cfop.py
AJUSTES_CONFIRMADOS_ST = {6108: True, 6202: True}


def carregar_cfop():
    cfop = pd.read_csv(DATA_DIR / "cfop.csv")
    cfop["is_st_efetivo"] = cfop["is_st"]
    for codigo, valor in AJUSTES_CONFIRMADOS_ST.items():
        cfop.loc[cfop["codigo"] == codigo, "is_st_efetivo"] = valor
    return cfop.set_index("codigo")[["is_st_efetivo", "is_transferencia"]].to_dict("index")


def classificar(cfop_map, codigo):
    info = cfop_map.get(codigo, {"is_st_efetivo": False, "is_transferencia": False})
    return info["is_st_efetivo"], info["is_transferencia"]


def calcular(entrada_path, saida_path, ciap_dae, ajustes_cfop):
    cfop_map = carregar_cfop()

    ent = pd.read_excel(entrada_path, sheet_name="Report", header=0, engine="xlrd")
    ent.columns = COLS_ENTRADA
    sai = pd.read_excel(saida_path, sheet_name="Report", header=0, engine="xlrd")
    sai.columns = COLS_SAIDA

    ent["is_st"], ent["is_transf"] = zip(*ent["CFOP"].map(lambda c: classificar(cfop_map, c)))
    sai["is_st"], sai["is_transf"] = zip(*sai["CFOP"].map(lambda c: classificar(cfop_map, c)))

    ent_calc = ent[~(ent["is_st"] & ent["is_transf"])]
    # --ajuste-cfop cobre CFOPs lançados direto no sistema contábil, fora do relatório de NF (ex: 1602)
    ajuste_credito_total = sum(ajustes_cfop.values())
    linha05 = ent_calc["ValorICMS"].sum() + ajuste_credito_total
    linha03 = ent_calc[ent_calc["is_st"]]["ValorICMS"].sum()

    sai_calc = sai[~(sai["is_st"] & sai["is_transf"])]
    linha02 = sai_calc["ValorICMS"].sum()
    linha07 = sai_calc[sai_calc["is_st"]]["ValorICMS"].sum()

    linha01 = 0.0
    linha04 = linha01 + linha02 + linha03
    linha06 = sum(ciap_dae)
    linha08 = linha05 + linha06 + linha07
    saldo = linha08 - linha04
    imposto_a_recolher = -saldo if saldo < 0 else 0.0
    saldo_credor = saldo if saldo >= 0 else 0.0

    return {
        "01": linha01, "02": linha02, "03": linha03, "04": linha04,
        "05": linha05, "06": linha06, "07": linha07, "08": linha08,
        "13_imposto_a_recolher": imposto_a_recolher, "14_saldo_credor": saldo_credor,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--entrada", required=True)
    p.add_argument("--saida", required=True)
    p.add_argument("--ciap", type=float, action="append", default=[],
                    help="valor de CIAP/DAE Antecipado do período (repita para cada valor)")
    p.add_argument("--dae", type=float, action="append", default=[],
                    help="alias de --ciap, para clareza na linha de comando")
    p.add_argument("--ajuste-cfop", action="append", default=[],
                    help="CFOP:valor de crédito lançado fora do relatório (ex: 1602:3814.87)")
    p.add_argument("--referencia-imposto-a-recolher", type=float, default=None)
    args = p.parse_args()

    ajustes = {}
    for item in args.ajuste_cfop:
        cfop_s, valor_s = item.split(":")
        ajustes[int(cfop_s)] = float(valor_s)

    resultado = calcular(args.entrada, args.saida, args.ciap + args.dae, ajustes)

    print("Resultado calculado:")
    for k, v in resultado.items():
        print(f"  {k}: R$ {v:,.2f}")

    if args.referencia_imposto_a_recolher is not None:
        diff = resultado["13_imposto_a_recolher"] - args.referencia_imposto_a_recolher
        print(f"\nReferência (Rotina 1025, linha 13): R$ {args.referencia_imposto_a_recolher:,.2f}")
        print(f"Diferença: R$ {diff:,.2f}")
        sys.exit(0 if abs(diff) < 0.05 else 1)


if __name__ == "__main__":
    main()
