import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st
from lib.auth import require_login, logout_button
from lib.db import get_session
from sqlalchemy import text

st.set_page_config(page_title="CFOP", layout="wide")
require_login()
logout_button()
st.title("Tabela de CFOP")
st.caption(
    "`is_st_padrao` é inferido automaticamente da descrição oficial do CFOP. O export de origem trunca "
    "descrições longas, o que já escondeu a palavra-chave de ST em pelo menos 2 códigos (6108, 6202) — "
    "descobertos só porque batemos o cálculo contra o livro fiscal real. Use o ajuste manual sempre que "
    "desconfiar que o padrão está errado para um CFOP específico."
)

session = get_session()

with st.expander("Buscar / filtrar"):
    busca = st.text_input("Código ou parte da descrição")

query = "select codigo, descricao, is_st_padrao, is_st_ajuste, is_transferencia, regra_especial from cfop"
params = {}
if busca:
    query += " where cast(codigo as text) like :b or descricao ilike :b2"
    params = {"b": f"%{busca}%", "b2": f"%{busca}%"}
query += " order by codigo"

rows = session.execute(text(query), params).mappings().all()
st.dataframe(rows, use_container_width=True, height=500)

st.markdown("---")
st.subheader("Ajustar um CFOP")
codigo = st.number_input("Código CFOP", min_value=1000, max_value=6999, step=1)
cfop_atual = session.execute(text("select * from cfop where codigo = :c"), {"c": codigo}).mappings().first()
if cfop_atual:
    st.write(f"**{cfop_atual['descricao']}** — padrão: {'ST' if cfop_atual['is_st_padrao'] else 'não-ST'}")
    ajuste = st.selectbox(
        "Ajuste manual de is_st",
        options=["(usar padrão)", "Forçar ST", "Forçar não-ST"],
        index=0 if cfop_atual["is_st_ajuste"] is None else (1 if cfop_atual["is_st_ajuste"] else 2),
    )
    regra = st.text_area("Regra especial / observação (ex: exceção do CFOP 5927)",
                          value=cfop_atual["regra_especial"] or "")
    if st.button("Salvar ajuste"):
        valor = None if ajuste == "(usar padrão)" else (ajuste == "Forçar ST")
        session.execute(text("""
            update cfop set is_st_ajuste = :v, regra_especial = :r, updated_at = now() where codigo = :c
        """), {"v": valor, "r": regra or None, "c": codigo})
        session.commit()
        st.success("Ajuste salvo.")
        st.rerun()
else:
    st.info("Código não encontrado na tabela — rode scripts/seed_cfop.py primeiro.")
