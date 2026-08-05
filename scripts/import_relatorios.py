"""
Importa os relatórios de Entrada e/ou Saída (.xls, aba "Report") para a tabela notas_fiscais_itens de
uma competência (empresa + ano + mês). Wrapper de linha de comando sobre app/lib/importacao.py (a mesma
lógica também é usada pela página Streamlit "Importar Relatórios").

Uso:
    python scripts/import_relatorios.py --empresa-cnpj 07.342.785/0005-53 --ano 2026 --mes 7 \
        --entrada caminho/RELATORIO_ENTRADA.xls --saida caminho/RELATORIO_SAIDA.xls [--substituir]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.lib.db import get_session  # noqa: E402
from app.lib.importacao import importar  # noqa: E402


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--empresa-cnpj", required=True)
    p.add_argument("--ano", type=int, required=True)
    p.add_argument("--mes", type=int, required=True)
    p.add_argument("--entrada", help="caminho do relatório de Entrada (.xls)")
    p.add_argument("--saida", help="caminho do relatório de Saída (.xls)")
    p.add_argument("--substituir", action="store_true")
    args = p.parse_args()

    session = get_session()
    try:
        resultado = importar(session, args.empresa_cnpj, args.ano, args.mes,
                              args.entrada, args.saida, args.substituir)
    except ValueError as e:
        raise SystemExit(str(e))
    print(resultado)


if __name__ == "__main__":
    main()
