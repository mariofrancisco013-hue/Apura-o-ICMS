"""
Carrega a tabela de referência de NCM (data/ncm.csv, extraída da Tabela NCM Vigente do sistema Classif do
governo em 06/08/2026, com a descrição hierárquica já reconstruída — ver sql/005_ncm.sql) no banco.

Uso (rode no SEU computador, com DATABASE_URL configurado — esta tabela tem 10.515 linhas, grande demais
para colar em SQL manualmente no painel do Supabase):
    python scripts/seed_ncm.py

Pode rodar de novo sem medo: o script apaga a tabela `ncm` inteira e recarrega do zero a cada execução
(é só uma tabela de referência, não tem dado editado pelo usuário para se perder).
"""
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.lib.db import get_session  # noqa: E402

DATA_CSV = Path(__file__).resolve().parent.parent / "data" / "ncm.csv"


def main():
    df = pd.read_csv(DATA_CSV, dtype={"codigo": str})
    session = get_session()
    session.execute(text("truncate table ncm"))
    df.to_sql("ncm", session.bind, if_exists="append", index=False, method="multi", chunksize=1000)
    session.commit()
    print(f"{len(df)} códigos de NCM carregados.")


if __name__ == "__main__":
    main()
