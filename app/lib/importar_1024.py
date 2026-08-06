"""
Leitura automática do PDF da Rotina 1024 (Livro Registro de Apuração do ICMS - RAICMS - Modelo P9) para
preencher a conferência por CFOP sem digitar/colar valor a valor.

Layout do PDF (confirmado em 06/08/2026 com o arquivo real da Sodine, "APURAÇÃO 1024 06.2026 F3.pdf"):
cada CFOP aparece como uma linha de texto simples nas seções "Entradas" e "Saídas", assim:

    0 1102 4.166,01 4.166,01 833,19 0,00 0,00
    │ │    │         │        │      │     └─ Outras
    │ │    │         │        │      └─ Isentas/Não Tributadas
    │ │    │         │        └─ Imposto Creditado/Debitado  (o que interessa comparar com valor_icms)
    │ │    │         └─ Base de Cálculo                       (o que interessa comparar com base_icms)
    │ │    └─ Valores Contábeis (não usado na conferência)
    │ └─ CFOP ("Fiscal")
    └─ Código "Contabil" (sempre 0 nos arquivos vistos até agora)

Isso também explica por que a grade de conferência não trazia todos os CFOPs antes: vários CFOPs da
Rotina 1024 (ex: 1353, 1407, 1409, 1602, 1933, 2353, 5202, 5409, 5949) têm Base de Cálculo = 0,00 no PDF —
o valor inteiro cai em "Outras" (não gera crédito/débito, ex: devolução, transferência ST, ajuste lançado
direto no contábil como o CFOP 1602). Como esses CFOPs às vezes nem aparecem no relatório de Entrada/Saída
importado (não passam por nota fiscal no fluxo normal), eles não existem em notas_fiscais_itens — e a
grade antiga só mostrava CFOPs que vinham do cálculo. A correção (ver app/lib/planilha.py) troca isso por
uma junção completa: todo CFOP que aparecer OU no calculado OU na Rotina 1024 aparece na grade.
"""
import re
from decimal import Decimal

import pdfplumber

# "<contabil> <cfop de 4 dígitos> <contábeis> <base> <imposto> <isentas> <outras>", tudo em uma linha.
_LINHA_CFOP_RE = re.compile(
    r"^\d+\s+(\d{4})\s+([\d.]+,\d{2})\s+([\d.]+,\d{2})\s+([\d.]+,\d{2})\s+([\d.]+,\d{2})\s+([\d.]+,\d{2})$"
)


def _para_decimal(s: str) -> Decimal:
    return Decimal(s.replace(".", "").replace(",", "."))


def parse_rotina_1024(arquivo) -> list[dict]:
    """`arquivo` é um caminho ou um buffer tipo st.file_uploader (PDF do RAICMS Modelo P9). Devolve uma
    lista de dicts {cfop, valor_base, valor_icms} — um por código de CFOP encontrado nas seções de
    Entradas e Saídas (ignora as linhas de "Sub Totais"/"Totais", que não têm CFOP de 4 dígitos isolado).
    Lança ValueError se nenhuma linha reconhecível for encontrada (arquivo no layout errado)."""
    resultado = []
    with pdfplumber.open(arquivo) as pdf:
        for page in pdf.pages:
            texto = page.extract_text() or ""
            for linha in texto.split("\n"):
                m = _LINHA_CFOP_RE.match(linha.strip())
                if not m:
                    continue
                cfop = int(m.group(1))
                if not (1000 <= cfop <= 7999):
                    continue  # evita casar números que não são CFOP por coincidência de formato
                resultado.append({
                    "cfop": cfop,
                    "valor_base": _para_decimal(m.group(3)),   # "Base de Cálculo"
                    "valor_icms": _para_decimal(m.group(4)),   # "Imposto Creditado"/"Imposto Debitado"
                })
    if not resultado:
        raise ValueError(
            "Não encontrei nenhuma linha de CFOP reconhecível neste PDF. Confira se é o arquivo certo "
            "(Livro Registro de Apuração do ICMS - RAICMS - Modelo P9) — o layout esperado é uma linha de "
            "texto por CFOP nas seções Entradas/Saídas, como '0 1102 4.166,01 4.166,01 833,19 0,00 0,00'."
        )
    return resultado
