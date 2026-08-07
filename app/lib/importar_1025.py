"""
Leitura automática do PDF da Rotina 1025 (Livro Registro de Apuração do ICMS, o mesmo layout oficial em
duas páginas — "DÉBITO DO IMPOSTO"/"CRÉDITO DO IMPOSTO" na página 1, "APURAÇÃO DO SALDO" na página 2) —
pedido do usuário em 07/08/2026, pra preencher a conferência com a Rotina 1025 sem digitar valor a valor
(mesma ideia já feita para a Rotina 1024 em app/lib/importar_1024.py).

Layout do PDF (confirmado com o arquivo real da Sodine, "1025 F3.pdf", competência 07/2026): a tabela tem
duas colunas de valor à direita — "Coluna Auxiliar" (usada nos itens discriminados de "02-Outros Débitos",
"03-Estorno de Créditos", "06-Outros Créditos" e "07-Estorno de Débitos") e "Somas" (o total de cada uma
das 14 linhas numeradas do livro, 01 a 14). Extrair por posição/rótulo de texto é frágil aqui porque o PDF
desalinha visualmente o valor da linha "01" (ele aparece, no texto extraído, ao lado do rótulo "02-" por
causa de como a célula mesclada da linha "01" é desenhada) — então em vez disso lemos TODOS os números da
coluna "Somas" (identificados pela posição x, bem à direita, separada da "Coluna Auxiliar") na ordem em que
aparecem na página, de cima para baixo: são exatamente 14 valores, um por linha do livro (01 a 14, nessa
ordem, mesmo atravessando as duas páginas do PDF) — confirmado batendo com a aritmética oficial (04 = 01 +
02 + 03, 08 = 05 + 06 + 07, etc.) no arquivo real de 07/2026.

Se o PDF não tiver exatamente 14 valores nessa coluna (layout diferente do esperado), a função lança
ValueError em vez de arriscar mapear errado — nesse caso o analista preenche manualmente na grade, como já
era feito antes desse parser existir.
"""
import re
from decimal import Decimal

import pdfplumber

_RE_VALOR = re.compile(r"^-?[\d.]+,\d{2}$")
_LINHAS_EM_ORDEM = ["01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11", "12", "13", "14"]
_X0_MINIMO_PADRAO = 480  # fallback se não achar o cabeçalho "Somas" pra calibrar


def _para_decimal(s: str) -> Decimal:
    return Decimal(s.replace(".", "").replace(",", "."))


def parse_rotina_1025(arquivo) -> dict:
    """`arquivo` é um caminho ou um buffer tipo st.file_uploader (PDF do Livro Registro de Apuração do
    ICMS - Rotina 1025). Devolve um dict {"01": Decimal, ..., "14": Decimal} com o valor da coluna "Somas"
    de cada uma das 14 linhas do livro. Lança ValueError se não encontrar exatamente 14 valores nessa
    coluna (layout inesperado — evita mapear valor errado silenciosamente)."""
    valores_em_ordem = []
    with pdfplumber.open(arquivo) as pdf:
        # calibra o limite x da coluna "Somas" pelo cabeçalho da própria página, se existir; senão usa o
        # padrão confirmado no arquivo real.
        x0_minimo = _X0_MINIMO_PADRAO
        for page in pdf.pages:
            for w in page.extract_words():
                if w["text"] == "Somas":
                    x0_minimo = w["x0"] - 15
                    break
            else:
                continue
            break

        for page in pdf.pages:
            candidatos = [
                w for w in page.extract_words()
                if _RE_VALOR.match(w["text"]) and w["x0"] > x0_minimo
            ]
            candidatos.sort(key=lambda w: w["top"])
            valores_em_ordem.extend(_para_decimal(w["text"]) for w in candidatos)

    if len(valores_em_ordem) != len(_LINHAS_EM_ORDEM):
        raise ValueError(
            f"Esperava encontrar 14 valores na coluna 'Somas' do PDF (um por linha do livro, 01 a 14), mas "
            f"encontrei {len(valores_em_ordem)}. O layout deste PDF pode ser diferente do esperado — "
            f"confira se é o arquivo certo (Livro Registro de Apuração do ICMS) ou preencha manualmente na "
            f"grade abaixo."
        )
    return dict(zip(_LINHAS_EM_ORDEM, valores_em_ordem))
