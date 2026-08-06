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

st.markdown(
    "**Para que serve esta página:** todo item importado (Entrada ou Saída) tem um código de CFOP. "
    "É esse código que diz ao sistema, automaticamente, duas coisas: **(1)** se a operação é de "
    "mercadoria sujeita à Substituição Tributária (ST) — o que muda como o item entra na apuração "
    "(vira estorno em vez de crédito/débito normal) — e **(2)** se é uma operação de transferência "
    "entre empresas do grupo, que só é válida sem sinalização se as duas empresas forem vinculadas "
    "(mesma raiz de CNPJ). Esta tabela é onde você confere e corrige essa classificação por CFOP."
)

with st.expander("O que cada coluna significa", expanded=True):
    st.markdown(
        "- **codigo** — o código CFOP (ex: 5.102, 1.403), lançado nos itens de Entrada/Saída.\n"
        "- **descricao** — a descrição oficial do CFOP, extraída da tabela de referência oficial.\n"
        "- **É ST pela descrição?** — o que o sistema concluiu automaticamente ao ler a descrição "
        "oficial (ex: se a descrição menciona 'substituição tributária', é marcado como ST). Esta "
        "coluna **não é editável** — ela é o ponto de partida, não a palavra final.\n"
        "- **Ajuste manual** — use esta coluna quando a descrição oficial não conta a história toda. "
        "Já aconteceu de um CFOP com descrição genérica (ex: 6108, 6202) ser, na prática, usado pela "
        "empresa só para mercadoria ST — nesse caso a classificação automática erra e precisa do "
        "ajuste manual para a apuração bater com o livro fiscal real. Deixe em branco/'usar padrão' "
        "sempre que a descrição já for suficiente.\n"
        "- **Transferência?** — marca se o CFOP é usado para transferência de mercadoria entre "
        "empresas do grupo (ex: 5.409, 6.409). Itens com esse CFOP são comparados contra o cadastro "
        "de Empresas: se as duas partes não forem vinculadas (mesma raiz de CNPJ), o sistema sinaliza "
        "na aba Inconsistências, dentro de ICMS Normal.\n"
        "- **Regra especial** — anotação livre para casos fora do padrão. Exemplo já registrado: o "
        "CFOP 5927 tem destaque de ICMS que parece 'errado' à primeira vista, mas é válido porque o "
        "estorno do crédito correspondente acontece em outro lançamento — sem essa nota, alguém "
        "revisando a apuração poderia sinalizar esse CFOP por engano."
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
st.dataframe(
    rows, use_container_width=True, height=500,
    column_config={
        "codigo": st.column_config.NumberColumn("Código"),
        "descricao": st.column_config.TextColumn("Descrição oficial", width="large"),
        "is_st_padrao": st.column_config.CheckboxColumn("É ST pela descrição?"),
        "is_st_ajuste": st.column_config.CheckboxColumn("Ajuste manual (ST)"),
        "is_transferencia": st.column_config.CheckboxColumn("Transferência?"),
        "regra_especial": st.column_config.TextColumn("Regra especial", width="medium"),
    },
    hide_index=True,
)

st.markdown("---")
st.subheader("Ajustar um CFOP")
st.caption(
    "Use isto só quando descobrir (normalmente ao conferir a apuração contra a Rotina 1024/1025) que a "
    "classificação automática de ST está errada para um CFOP específico, ou para deixar registrada uma "
    "regra especial como a do CFOP 5927."
)
codigo = st.number_input("Código CFOP", min_value=1000, max_value=6999, step=1)
cfop_atual = session.execute(text("select * from cfop where codigo = :c"), {"c": codigo}).mappings().first()
if cfop_atual:
    st.write(
        f"**{cfop_atual['descricao']}** — classificação automática pela descrição: "
        f"{'ST (substituição tributária)' if cfop_atual['is_st_padrao'] else 'não-ST'}"
    )
    ajuste = st.selectbox(
        "Ajuste manual de ST",
        options=["(usar classificação automática)", "Forçar como ST", "Forçar como não-ST"],
        index=0 if cfop_atual["is_st_ajuste"] is None else (1 if cfop_atual["is_st_ajuste"] else 2),
        help=(
            "Só mude isto se souber, por evidência concreta (ex: a apuração não bateu com a Rotina "
            "1024/1025 por causa deste CFOP), que a descrição oficial não reflete como esta empresa "
            "usa o código na prática."
        ),
    )
    regra = st.text_area(
        "Regra especial / observação (ex: exceção do CFOP 5927)",
        value=cfop_atual["regra_especial"] or "",
        help="Texto livre, aparece na tabela acima para quem for revisar depois — explique o porquê, não só o quê.",
    )
    if st.button("Salvar ajuste"):
        valor = None if ajuste == "(usar classificação automática)" else (ajuste == "Forçar como ST")
        session.execute(text("""
            update cfop set is_st_ajuste = :v, regra_especial = :r, updated_at = now() where codigo = :c
        """), {"v": valor, "r": regra or None, "c": codigo})
        session.commit()
        st.success("Ajuste salvo.")
        st.rerun()
else:
    st.info("Código não encontrado na tabela — rode scripts/seed_cfop.py primeiro (ou confira sql/002_seed_cfop.sql).")
