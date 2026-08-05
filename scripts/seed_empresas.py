"""
Carrega o cadastro de empresas do grupo (data/empresas.csv, recebido do usuário em 05/08/2026) no banco.

Uso:
    python scripts/seed_empresas.py
"""
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.lib.db import get_session  # noqa: E402
from sqlalchemy import text  # noqa: E402

DATA_CSV = Path(__file__).resolve().parent.parent / "data" / "empresas.csv"


def main():
    with DATA_CSV.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    session = get_session()
    for r in rows:
        session.execute(text("""
            insert into empresas (filial_winthor, razao_social, cnpj, inscricao_estadual,
                                   inscricao_municipal, uf, regime, is_empresa_apurada)
            values (:filial, :razao, :cnpj, :ie, :im, :uf, :regime, :apurada)
            on conflict (cnpj) do update
                set razao_social = excluded.razao_social,
                    filial_winthor = excluded.filial_winthor,
                    inscricao_estadual = excluded.inscricao_estadual,
                    inscricao_municipal = excluded.inscricao_municipal,
                    uf = excluded.uf,
                    regime = excluded.regime,
                    is_empresa_apurada = excluded.is_empresa_apurada,
                    updated_at = now()
        """), {
            "filial": r["filial_winthor"] or None,
            "razao": r["razao_social"],
            "cnpj": r["cnpj"],
            "ie": r["inscricao_estadual"] or None,
            "im": r["inscricao_municipal"] or None,
            "uf": r["uf"] or None,
            "regime": r["regime"] or None,
            "apurada": r["is_empresa_apurada"].lower() == "true",
        })
    session.commit()
    print(f"{len(rows)} empresas carregadas/atualizadas.")


if __name__ == "__main__":
    main()
