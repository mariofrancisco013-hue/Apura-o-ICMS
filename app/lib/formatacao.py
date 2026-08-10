"""
Formatação de valores em moeda contábil brasileira (R$ 1.234,56) — pedido do usuário em 06/08/2026: os
valores apareciam como número puro (ex: 1234.56) ou em formato americano (ex: "1,234.56" — separador de
milhar e decimal invertidos), o que confunde quem está acostumado com o padrão contábil brasileiro.

Duas formas de uso:
- `formatar_moeda(valor)`: converte um número para a string "R$ 1.234,56" — para tabelas só de leitura
  (st.dataframe, st.table), onde não tem problema virar texto.
- `coluna_moeda(label, **kwargs)`: um atalho para st.column_config.NumberColumn com o prefixo "R$" — para
  grades editáveis (st.data_editor), onde a coluna precisa continuar numérica para dar para editar. O
  agrupador de milhar brasileiro (ponto) não é suportado pelo componente de coluna numérica do Streamlit
  nesses casos, então o resultado fica "R$ 1234.56" (com ponto decimal) em vez de "R$ 1.234,56" — é a
  limitação do componente, não afeta o valor gravado.
"""


def formatar_moeda(valor) -> str:
    """Formata um número como moeda contábil brasileira: R$ 1.234,56. Aceita None/valores inválidos sem
    quebrar a tela (mostra R$ 0,00)."""
    try:
        v = float(valor)
    except (TypeError, ValueError):
        v = 0.0
    texto_us = f"{v:,.2f}"  # "1,234.56" (separador de milhar "," e decimal ".")
    texto_br = texto_us.replace(",", "_").replace(".", ",").replace("_", ".")  # -> "1.234,56"
    sinal = ""
    if texto_br.startswith("-"):
        sinal, texto_br = "-", texto_br[1:]
    return f"{sinal}R$ {texto_br}"


def coluna_moeda(label: str, **kwargs):
    """Atalho para uma coluna numérica de grade editável (st.data_editor) exibida com prefixo R$."""
    import streamlit as st
    kwargs.setdefault("format", "R$ %.2f")
    return st.column_config.NumberColumn(label, **kwargs)


def rotulo_empresa(e) -> str:
    """Rótulo padrão para todo seletor de empresa da aplicação (Importar Relatórios, ICMS PE, Empresas —
    excluir) — pedido do usuário em 10/08/2026: "ajuste o filtro para selecionar a empresa pela filial do
    Winthor", com a ordem "filial, cnpj e nome da empresa" confirmada pelo usuário. Filial primeiro porque
    é o código mais curto, o que o analista já reconhece de cabeça vindo do Winthor — mais rápido de achar
    digitando no menu do que procurar pela razão social inteira.

    `e` precisa ter as chaves filial_winthor, cnpj, razao_social (linha de `select ... from empresas`, com
    o filial_winthor incluído na consulta — sem ele o rótulo não tem o que mostrar). Filial em branco (campo
    opcional no cadastro) mostra "(sem filial)" no lugar, para a empresa continuar aparecendo na lista."""
    filial = e["filial_winthor"] if e["filial_winthor"] else "(sem filial)"
    return f"{filial} — {e['cnpj']} — {e['razao_social']}"
