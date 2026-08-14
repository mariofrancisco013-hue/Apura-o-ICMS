import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st
import pandas as pd
from lib.auth import require_login, logout_button, usuario_atual
from lib.db import get_session
from lib.importacao import buscar_competencia, get_or_create_competencia
from lib.icms_adicional10 import (
    parse_filtro_clientes, salvar_clientes_filtro, carregar_clientes_filtro, salvar_cadastro_clientes_editado,
    filtrar_apenas_excecoes,
    parse_nfes, parse_relatorio_10, salvar_nfes_por_competencia, carregar_nfes_itens,
    carregar_faturamento, salvar_faturamento, parse_resumo_faturamento,
    listar_cfop_venda_ajustes, salvar_cfop_venda_ajustes, calcular_faturamento_cfop_venda,
    calcular_adicional10, PCT_FATURAMENTO_LIMITE, PCT_BASE_ADICIONAL_1, PCT_BASE_ADICIONAL_4,
    ALIQ_ADICIONAL_1, ALIQ_ADICIONAL_4,
)
from lib.formatacao import formatar_moeda, rotulo_empresa
from sqlalchemy import text
import io

st.set_page_config(page_title="Adicional 10%", layout="wide")
require_login()
logout_button()
st.title("Adicional 10%")
st.caption(
    "Pedido do usuário em 13/08/2026, usando a lógica da planilha real de apuração (\"ADICIONAL 10  "
    "ATACADO F3.xls\"). Calcula, por competência: **VENDAS** (soma do Valor Total das NFs de clientes que "
    "**não** estão cadastrados como exceção) menos "
    f"**{PCT_FATURAMENTO_LIMITE:.0f}% do Faturamento** do mês = **Base de Cálculo**. Sobre a Base, dois "
    f"adicionais de ICMS: **{PCT_BASE_ADICIONAL_1:.2f}%** da base a **{ALIQ_ADICIONAL_1:.0f}%**, e "
    f"**{PCT_BASE_ADICIONAL_4:.2f}%** da base a **{ALIQ_ADICIONAL_4:.0f}%** — proporção fixa extraída da "
    "planilha real do usuário."
)

session = get_session()

empresas = session.execute(text(
    "select id, filial_winthor, razao_social, cnpj from empresas order by filial_winthor, razao_social"
)).mappings().all()
if not empresas:
    st.warning("Nenhuma empresa cadastrada ainda. Cadastre em **Empresas** antes de continuar.")
    st.stop()

col1, col2, col3 = st.columns(3)
empresa = col1.selectbox("Empresa", empresas, format_func=rotulo_empresa)
ano = col2.number_input("Ano", min_value=2020, max_value=2100, value=2026, step=1)
mes = col3.number_input("Mês", min_value=1, max_value=12, value=7, step=1)

empresa_id = empresa["id"]

# Só CONSULTA se a competência já existe — mesmo motivo documentado em app/lib/importacao.py::buscar_competencia
# (não criar competência vazia só por navegar Empresa/Ano/Mês).
cid = buscar_competencia(session, empresa["cnpj"], int(ano), int(mes), modulo="icms_adicional_10")

if cid is None:
    st.caption(
        f"Competência: **{empresa['razao_social']} — {mes:02d}/{ano}** — ainda não criada (nada importado "
        f"ainda nesta competência)."
    )
else:
    st.caption(f"Competência: **{empresa['razao_social']} — {mes:02d}/{ano}**.")

st.markdown("---")

aba_cadastro, aba_importar, aba_calculo = st.tabs(
    ["📇 Cadastro de Clientes", "📥 Importar Planilha", "🧮 Cálculo do Mês"]
)

with aba_cadastro:
    st.caption(
        "Esta lista é só de **exceções** — clientes cujas NFs **não** devem contar em VENDAS. Cliente "
        "**ausente** daqui conta normalmente por padrão (mudança de 14/08/2026 a pedido do usuário: antes "
        "era o contrário — só contava quem estivesse marcado \"Sim\"). Cadastro **global**, vale para "
        "**todas as competências**, não é por mês."
    )

    with st.expander("📥 Importar exceções de uma planilha (em vez de digitar linha por linha)"):
        st.caption(
            "Aceita **qualquer planilha simples** com 2 ou 3 colunas, nesta ordem: **Código do Cliente**, "
            "**Cliente** (nome, opcional) — ou, se quiser reaproveitar a planilha antiga de cadastro (aba "
            "FILTRO, 3 colunas Código/Classificação/Cliente), também funciona: só as linhas marcadas "
            "**\"Exceção\"** são importadas, o resto (\"Sim\", em branco) é ignorado, já que agora é o "
            "padrão. Atualiza quem já existe e adiciona quem é novo — não apaga ninguém que não vier no "
            "arquivo."
        )
        arq_cadastro = st.file_uploader(
            "Planilha de exceções (.xls/.xlsx)", type=["xls", "xlsx"], key="upload_cadastro_adicional10"
        )
        if st.button(
            "📥 Importar exceções", key="btn_importar_cadastro_adicional10", disabled=arq_cadastro is None
        ):
            try:
                df_filtro_bruto = parse_filtro_clientes(arq_cadastro)
                # Se a coluna "Classificação" não veio (planilha só com 2 colunas: Código/Cliente), toda
                # linha é exceção por definição — não faz sentido filtrar. Se veio (reaproveitando o
                # formato antigo de 3 colunas), filtra só quem está marcado "Exceção".
                tem_classificacao = df_filtro_bruto["calcula"].notna().any()
                df_excecoes = filtrar_apenas_excecoes(df_filtro_bruto) if tem_classificacao else df_filtro_bruto
                n_cad = salvar_clientes_filtro(session, df_excecoes)
                ignoradas = len(df_filtro_bruto) - len(df_excecoes)
                msg = f"{n_cad} exceção(ões) salva(s) no cadastro."
                if ignoradas:
                    msg += f" {ignoradas} linha(s) ignorada(s) (não marcada(s) \"Exceção\")."
                st.success(msg)
                st.rerun()
            except ValueError as e:
                st.error(str(e))

    clientes_atuais = carregar_clientes_filtro(session)
    st.caption(
        f"{len(clientes_atuais)} exceção(ões) cadastrada(s). Use o ícone de busca 🔍 no canto superior da "
        "grade para filtrar por código ou nome."
    )
    clientes_editado = st.data_editor(
        clientes_atuais[["cod_cliente", "cliente_nome"]], use_container_width=True, num_rows="dynamic",
        height=500, key="editor_cadastro_clientes_adicional10",
        column_config={
            "cod_cliente": st.column_config.NumberColumn("Código", required=True),
            "cliente_nome": st.column_config.TextColumn("Cliente"),
        },
        column_order=["cod_cliente", "cliente_nome"],
    )
    st.caption(
        "Para incluir uma exceção: adicione uma linha nova (ícone + no final da grade) e digite o código "
        "do cliente. Para remover (o cliente volta a contar normalmente): selecione a linha e aperte o "
        "ícone de lixeira. Depois clique em Salvar — vale na hora para o Cálculo do Mês (recalcula sozinho "
        "ao trocar de aba)."
    )
    if st.button("💾 Salvar cadastro de clientes", key="btn_salvar_cadastro_adicional10"):
        resultado_cad = salvar_cadastro_clientes_editado(
            session, clientes_atuais[["cod_cliente", "cliente_nome"]], clientes_editado
        )
        st.success(
            f"{resultado_cad['salvos']} exceção(ões) salva(s), {resultado_cad['removidos']} removida(s)."
        )
        st.rerun()

with aba_importar:
    st.caption(
        "Aceita dois formatos, detectados automaticamente: a **planilha consolidada** do analista (abas "
        "**FILTRO** + **NFES** + **RESUMO**) ou o **export bruto do Winthor** (sheet \"Report\", sem "
        "cabeçalho — ex: \"10 f3.xlsx\"), que alimenta só as NFs (nenhum cadastro de cliente vem nesse "
        "formato). Da aba **FILTRO**, só as linhas marcadas **\"Exceção\"** são salvas no cadastro (o "
        "resto — \"Sim\", em branco — é ignorado, já que agora é o padrão; ver aba **Cadastro de "
        "Clientes**); atualiza quem já existe e não apaga ninguém (pra remover uma exceção, use a aba "
        "Cadastro de Clientes). As NFs são agrupadas automaticamente pela **Data de Emissão** de cada "
        "linha e gravadas na competência certa (criando a competência se ainda não existir) — um único "
        "arquivo com vários meses alimenta todos eles de uma vez, sem precisar trocar o Ano/Mês acima "
        "antes de importar. O Faturamento (aba RESUMO, se presente) só serve pra pré-preencher o campo na "
        "aba Cálculo do Mês — continua editável depois."
    )

    arq_planilha = st.file_uploader(
        "Planilha Adicional 10% (.xls/.xlsx)", type=["xls", "xlsx"], key="upload_adicional10"
    )
    if st.button("📥 Importar planilha", key="btn_importar_adicional10", disabled=arq_planilha is None):
        try:
            # engine="calamine": mesmo motivo do resto do projeto — openpyxl quebra em exports reais do
            # Winthor (achado em produção em 13/08/2026, ver app/lib/icms_adicional10.py).
            xl = pd.ExcelFile(arq_planilha, engine="calamine")
            mensagens = []

            if "FILTRO" in xl.sheet_names or "NFES" in xl.sheet_names:
                # Planilha consolidada do analista.
                if "FILTRO" in xl.sheet_names:
                    df_filtro = parse_filtro_clientes(arq_planilha)
                    df_excecoes_import = filtrar_apenas_excecoes(df_filtro)
                    n_clientes = salvar_clientes_filtro(session, df_excecoes_import)
                    mensagens.append(
                        f"{n_clientes} exceção(ões) no cadastro (aba FILTRO — só as marcadas \"Exceção\")."
                    )
                else:
                    mensagens.append(
                        "Aba \"FILTRO\" não encontrada — cadastro de clientes não foi atualizado."
                    )

                if "NFES" in xl.sheet_names:
                    df_nfes = parse_nfes(arq_planilha)
                    resultado_import = salvar_nfes_por_competencia(
                        session, empresa["cnpj"], df_nfes, get_or_create_competencia
                    )
                    if resultado_import:
                        detalhe = ", ".join(
                            f"{qtd} NF(s) em {comp}" for comp, qtd in sorted(resultado_import.items())
                        )
                        mensagens.append(f"NFs importadas: {detalhe}.")
                    else:
                        mensagens.append("Nenhuma NF com Data de Emissão válida encontrada na aba NFES.")

                if "RESUMO" in xl.sheet_names:
                    faturamento_map = parse_resumo_faturamento(arq_planilha)
                    n_faturamento = 0
                    for (f_ano, f_mes), valor in faturamento_map.items():
                        cid_fat = get_or_create_competencia(
                            session, empresa["cnpj"], f_ano, f_mes, modulo="icms_adicional_10"
                        )
                        if carregar_faturamento(session, cid_fat) is None:
                            salvar_faturamento(session, cid_fat, valor)
                            n_faturamento += 1
                    if n_faturamento:
                        mensagens.append(
                            f"Faturamento pré-preenchido em {n_faturamento} competência(s) (só onde ainda "
                            f"não havia valor salvo — não sobrescreve edição manual já feita)."
                        )
            elif "Report" in xl.sheet_names:
                # Export bruto do Winthor — só NFs, sem cadastro de clientes nem faturamento.
                df_nfes = parse_relatorio_10(arq_planilha)
                resultado_import = salvar_nfes_por_competencia(
                    session, empresa["cnpj"], df_nfes, get_or_create_competencia
                )
                if resultado_import:
                    detalhe = ", ".join(
                        f"{qtd} NF(s) em {comp}" for comp, qtd in sorted(resultado_import.items())
                    )
                    mensagens.append(f"NFs importadas (export bruto do Winthor): {detalhe}.")
                else:
                    mensagens.append("Nenhuma NF com Data de Emissão válida encontrada no arquivo.")
            else:
                mensagens.append(
                    "Não foi possível reconhecer o formato da planilha — esperado uma aba \"FILTRO\"/\"NFES\" "
                    "(planilha consolidada) ou uma aba \"Report\" (export bruto do Winthor)."
                )

            st.success(" ".join(mensagens))
            st.rerun()
        except ValueError as e:
            st.error(str(e))

with aba_calculo:
    if cid is None:
        st.info(
            "Ainda não há competência criada para este Empresa/Ano/Mês — importe uma planilha na aba "
            "**Importar Planilha** primeiro."
        )
    else:
        st.markdown("#### Faturamento do mês")
        with st.expander("🔎 Buscar Faturamento do CFOP Venda (ICMS Normal)"):
            st.caption(
                "Soma o **Valor Total** das saídas da competência de **ICMS Normal** (mesma Empresa/Ano/"
                "Mês selecionada acima) cujo CFOP tem \"VENDA\" na descrição oficial. Ajuste abaixo se "
                "algum CFOP precisar entrar ou ficar de fora manualmente, independente da descrição."
            )
            faturamento_cfop, detalhe_cfop = calcular_faturamento_cfop_venda(
                session, empresa_id, int(ano), int(mes)
            )
            if faturamento_cfop is None:
                st.info(
                    "Nenhuma competência de **ICMS Normal** encontrada para essa Empresa/Ano/Mês — "
                    "importe a Planilha de Saída lá primeiro pra poder buscar o Faturamento daqui."
                )
            else:
                if not detalhe_cfop.empty:
                    detalhe_fmt = detalhe_cfop.copy()
                    detalhe_fmt["valor_total_fmt"] = detalhe_fmt["valor_total"].apply(
                        lambda v: formatar_moeda(v) if pd.notna(v) else "—"
                    )
                    detalhe_fmt["incluido_fmt"] = detalhe_fmt["incluido"].map({True: "✅ Sim", False: "❌ Não"})
                    st.dataframe(
                        detalhe_fmt.rename(columns={
                            "cfop": "CFOP", "descricao": "Descrição", "valor_total_fmt": "Valor Total",
                            "incluido_fmt": "Entra no Faturamento?",
                        })[["CFOP", "Descrição", "Valor Total", "Entra no Faturamento?"]],
                        use_container_width=True, hide_index=True, height=250,
                    )
                st.metric("Faturamento calculado (CFOP Venda)", formatar_moeda(faturamento_cfop))
                if st.button("⬇️ Usar este valor no campo Faturamento", key="btn_usar_faturamento_cfop"):
                    st.session_state["input_faturamento_adicional10"] = faturamento_cfop
                    st.success(
                        "Valor preenchido no campo Faturamento abaixo — clique em \"Salvar Faturamento\" "
                        "pra confirmar."
                    )
                    st.rerun()

                st.markdown("**Ajustar CFOPs (forçar incluir ou excluir manualmente)**")
                st.caption(
                    "Só precisa mexer aqui se algum CFOP estiver entrando errado ou faltando entrar. "
                    "Marque \"Incluir?\" e salve — o ajuste vale mais que a descrição automática, pra "
                    "sempre (todas as competências desta empresa), até você remover."
                )
                ajustes_atuais = listar_cfop_venda_ajustes(session, empresa_id)
                ajustes_editado = st.data_editor(
                    ajustes_atuais, use_container_width=True, num_rows="dynamic", hide_index=True,
                    key="editor_cfop_venda_ajuste_adicional10",
                    column_config={
                        "id": st.column_config.NumberColumn("ID", disabled=True),
                        "cfop": st.column_config.NumberColumn("CFOP", required=True),
                        "descricao": st.column_config.TextColumn("Descrição do CFOP", disabled=True,
                                                                  width="large"),
                        "incluir": st.column_config.CheckboxColumn("Incluir?", default=True),
                        "motivo": st.column_config.TextColumn("Motivo (opcional)"),
                        "criado_por_email": st.column_config.TextColumn("Ajustado por", disabled=True),
                        "created_at": st.column_config.DatetimeColumn("Ajustado em", disabled=True),
                    },
                    column_order=["cfop", "descricao", "incluir", "motivo", "criado_por_email",
                                  "created_at", "id"],
                )
                if st.button("💾 Salvar ajustes de CFOP", key="btn_salvar_cfop_venda_ajuste"):
                    resultado_ajuste = salvar_cfop_venda_ajustes(
                        session, empresa_id, ajustes_atuais, ajustes_editado, usuario=usuario_atual()
                    )
                    st.success(
                        f"{resultado_ajuste['salvos']} ajuste(s) salvo(s), "
                        f"{resultado_ajuste['removidos']} removido(s)."
                    )
                    st.rerun()

        faturamento_atual = carregar_faturamento(session, cid)
        faturamento_input = st.number_input(
            "Faturamento do mês (R$)",
            min_value=0.0, value=float(faturamento_atual) if faturamento_atual is not None else 0.0,
            step=1000.0, format="%.2f", key="input_faturamento_adicional10",
            help="Vem do botão \"Usar este valor\" acima, ou digite manualmente — as duas formas são "
                 "aceitas.",
        )
        if st.button("💾 Salvar Faturamento", key="btn_salvar_faturamento_adicional10"):
            salvar_faturamento(session, cid, faturamento_input)
            st.success("Faturamento salvo.")
            st.rerun()

        st.markdown("---")
        resultado = calcular_adicional10(session, cid, faturamento_atual)

        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("VENDAS (não-exceção)", formatar_moeda(resultado["vendas"]))
        m2.metric(f"{PCT_FATURAMENTO_LIMITE:.0f}% Faturamento", formatar_moeda(resultado["limite_10pct"]))
        m3.metric("Base de Cálculo", formatar_moeda(resultado["base_calculo"]))
        m4.metric(f"Adicional {ALIQ_ADICIONAL_1:.0f}%", formatar_moeda(resultado["adicional_1"]))
        m5.metric(f"Adicional {ALIQ_ADICIONAL_4:.0f}%", formatar_moeda(resultado["adicional_4"]))

        st.metric("💰 Total Adicional 10%", formatar_moeda(resultado["total"]))

        if faturamento_atual is None:
            st.warning(
                "Faturamento ainda não foi salvo para esta competência — Base de Cálculo está usando "
                "R$ 0,00."
            )

        detalhamento = resultado["detalhamento"]
        if not detalhamento.empty:
            st.subheader("NFs desta competência")
            tabela_nfes = detalhamento.copy()
            tabela_nfes["vl_total_fmt"] = tabela_nfes["vl_total"].apply(
                lambda v: formatar_moeda(v) if pd.notna(v) else "—"
            )
            tabela_nfes["conta_fmt"] = tabela_nfes["conta"].map({True: "✅ Sim", False: "❌ Não"})
            st.dataframe(
                tabela_nfes.rename(columns={
                    "nfe": "NFE", "emissao": "Emissão", "cod_cliente": "Cód. Cliente",
                    "cliente_nome": "Cliente", "uf": "UF", "vl_total_fmt": "Valor Total",
                    "conta_fmt": "Conta em VENDAS?",
                })[["NFE", "Emissão", "Cód. Cliente", "Cliente", "UF", "Valor Total", "Conta em VENDAS?"]],
                use_container_width=True, hide_index=True, height=450,
            )
        else:
            st.info("Nenhuma NF importada para esta competência ainda.")
