# Ajuda a aba "Ajustes" (lançamentos manuais) do ICMS Normal a excluir lançamentos — pedido do usuário em
# 13/08/2026: "no ICMS normal aba ajuste na apuração, como excluo ajustes lançados?". Até então a aba só
# tinha um formulário de ADICIONAR (`st.form`), sem nenhuma forma de excluir um lançamento errado — só dava
# pra corrigir apagando a linha direto no banco.
#
# Segue o mesmo padrão já usado em `lib/ncm_tributado.py` e `lib/cfops_sem_validacao.py`: uma grade editável
# (`st.data_editor` com `num_rows="dynamic"`) onde o usuário seleciona a linha e aperta o ícone de lixeira —
# a diferença aqui é que a INCLUSÃO continua só pelo formulário dedicado da página (que já valida
# tipo/CFOP corretamente); linha nova digitada direto na grade é ignorada por esta função.
from sqlalchemy import text
import pandas as pd


def excluir_lancamentos_removidos(session, df_original: pd.DataFrame, df_editado: pd.DataFrame) -> int:
    """Compara o antes/depois de uma (ou mais, já concatenadas) grade(s) `st.data_editor` e exclui do banco
    os lançamentos cujo `id` sumiu — removidos pelo usuário na grade (ícone de lixeira). Linhas novas
    adicionadas na grade (sem `id`) são ignoradas. Devolve a quantidade excluída."""
    ids_originais = set(df_original["id"].dropna().astype(int)) if not df_original.empty else set()
    ids_editados = (
        set(df_editado["id"].dropna().astype(int))
        if "id" in df_editado.columns and not df_editado.empty else set()
    )
    removidos = ids_originais - ids_editados
    for lancamento_id in removidos:
        session.execute(text("delete from lancamentos_manuais where id = :id"), {"id": int(lancamento_id)})
    if removidos:
        session.commit()
    return len(removidos)
