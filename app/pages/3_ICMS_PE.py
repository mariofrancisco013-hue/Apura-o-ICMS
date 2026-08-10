import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st
import pandas as pd
from lib.auth import require_login, logout_button, usuario_atual
from lib.db import get_session
from lib.importacao import buscar_competencia, get_or_create_competencia
from lib.importar_1024 import parse_rotina_1024
from lib.extrato_antecipado_pe import (
    parse_extrato_antecipado, salvar_extrato_antecipado, listar_extrato_antecipado, listar_nao_recuperavel,
)
from lib.cfops_antecipacao_pe import listar_cfops_antecipacao, salvar_cfops_antecipacao
from lib.calculo_icms_pe import (
    salvar_checkpoint_1024_pe, carregar_checkpoint_1024_pe,
    sugerir_valor_4101, carregar_valor_4101_manual, salvar_valor_4101_manual,
    carregar_valor_manual_pe, salvar_valor_manual_pe, remover_valor_manual_pe,
    competencia_anterior_id, calcular_apuracao_pe,
    listar_cfops_transferencia_checkpoint, carregar_confirmacao, salvar_confirmacao,
    comparar_com_checkpoint_1025_pe,
)
from lib.calculo_icms_normal import salvar_apuracao
from lib.importar_1025 import parse_rotina_1025
from lib.planilha import salvar_checkpoint_1025_bulk
from lib.formatacao import formatar_moeda, coluna_moeda, rotulo_empresa
from sqlalchemy import text

st.set_page_config(page_title="ICMS PE", layout="wide")
require_login()
logout_button()
st.title("ICMS PE — Crédito Presumido")
st.caption(
    "Apuração do regime de Crédito Presumido do atacadista (Decreto de PE) — modelo diferente do ICMS "
    "Normal: em vez de débito/crédito por CFOP, calcula Antecipação (imposto na entrada) + Crédito "
    "Presumido (benefício sobre a base de saídas). Fontes: Rotina 1024 (mesmo PDF do ICMS Normal) + "
    "Extrato de ICMS Antecipado do e-Fisco/PE."
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
mes = col3.number_input("Mês", min_value=1, max_value=12, value=6, step=1)

empresa_id = empresa["id"]

# Só CONSULTA se a competência já existe — não cria nada no banco (ver docstring de buscar_competencia em
# app/lib/importacao.py). A competência só é criada de fato no momento de uma ação real (importar um PDF,
# salvar um valor manual, calcular a apuração) via _garantir_competencia() logo abaixo — pedido do usuário
# em 10/08/2026: "quando eu avanço o mês ele cria uma nova apuração, sem eu ter importado nada".
cid = buscar_competencia(session, empresa["cnpj"], int(ano), int(mes), modulo="icms_antecipado")


def _garantir_competencia():
    """Chamar SÓ dentro de uma ação de gravação (botão de importar/salvar/calcular) — cria a competência no
    banco se ainda não existir, e atualiza a variável `cid` do script pro resto da execução deste rerun."""
    global cid
    if cid is None:
        cid = get_or_create_competencia(session, empresa["cnpj"], int(ano), int(mes), modulo="icms_antecipado")
    return cid


if cid is None:
    status = None
    st.caption(f"Competência: **{empresa['razao_social']} — {mes:02d}/{ano}** — ainda não criada (nada "
               f"importado/salvo ainda nesta competência).")
else:
    status = session.execute(text("select status from competencias where id = :cid"), {"cid": cid}).scalar()
    st.caption(f"Competência: **{empresa['razao_social']} — {mes:02d}/{ano}** (status: {status}).")

(aba_1024, aba_extrato, aba_nao_recuperavel, aba_cfops, aba_apuracao) = st.tabs([
    "📥 Rotina 1024", "📄 Extrato de ICMS Antecipado", "🚫 Não Recuperável (Extrato Fronteira)",
    "🔖 CFOPs de Antecipação", "📋 Apuração",
])

# ============================================================================================
with aba_1024:
    st.caption(
        "Mesmo PDF já usado no ICMS Normal (Livro RAICMS Modelo P9) — aqui reaproveitamos as colunas "
        "\"Valores Contábeis\", \"Base de Cálculo\" e \"Imposto Creditado/Debitado\" de todos os CFOPs de "
        "uma vez, sem digitar valor a valor."
    )
    c_up1, c_up2 = st.columns([3, 1])
    pdf_1024 = c_up1.file_uploader("PDF da Rotina 1024", type=["pdf"], key="upload_1024_pe",
                                    label_visibility="collapsed")
    if c_up2.button("📥 Importar do PDF", key="importar_1024_pe", disabled=pdf_1024 is None):
        try:
            cid = _garantir_competencia()
            linhas_1024 = parse_rotina_1024(pdf_1024)
            n = salvar_checkpoint_1024_pe(session, cid, linhas_1024)
            st.success(f"{n} CFOP(s) importado(s) do PDF da Rotina 1024 (Entrada + Saída juntas).")
            st.rerun()
        except ValueError as e:
            st.error(str(e))

    df_1024 = carregar_checkpoint_1024_pe(session, cid)
    if df_1024.empty:
        st.info("Nenhum dado da Rotina 1024 importado ainda para esta competência.")
    else:
        st.dataframe(
            df_1024.assign(
                valor_contabil=df_1024["valor_contabil"].apply(formatar_moeda),
                valor_base=df_1024["valor_base"].apply(formatar_moeda),
                valor_icms=df_1024["valor_icms"].apply(formatar_moeda),
            ),
            use_container_width=True,
        )

    # --------------------------------------------------------------------------------------
    # Conferência de CFOPs de transferência (pedido do usuário em 10/08/2026) — ao contrário do ICMS
    # Normal, o modelo de Crédito Presumido soma CFOP de transferência junto com os demais nas linhas
    # normais, então um CFOP de transferência errado na Rotina 1024 pode distorcer a apuração sem aviso
    # nenhum. Trava o cálculo (ver aba Apuração) até o analista confirmar explicitamente.
    # --------------------------------------------------------------------------------------
    cfops_transf = listar_cfops_transferencia_checkpoint(session, cid)
    if cfops_transf:
        confirmacao = carregar_confirmacao(session, cid, "cfop_transferencia_pe")
        st.markdown("---")
        if confirmacao is None:
            st.warning(
                f"⚠️ {len(cfops_transf)} CFOP(s) de transferência encontrado(s) na Rotina 1024 importada "
                f"(Entrada e/ou Saída) — confira se estão corretos antes de calcular a apuração."
            )
            st.dataframe(
                pd.DataFrame(cfops_transf).assign(
                    valor_contabil=lambda d: d["valor_contabil"].apply(formatar_moeda)
                )[["direcao", "cfop", "descricao", "valor_contabil"]],
                use_container_width=True,
            )
            c_conf1, c_conf2 = st.columns(2)
            if c_conf1.button("✅ Sim, está correto", key="confirmar_transf_sim"):
                salvar_confirmacao(session, cid, "cfop_transferencia_pe", True, usuario=usuario_atual())
                st.rerun()
            if c_conf2.button("❌ Não, preciso corrigir", key="confirmar_transf_nao"):
                salvar_confirmacao(session, cid, "cfop_transferencia_pe", False, usuario=usuario_atual())
                st.rerun()
        elif confirmacao["confirmado"]:
            st.success(
                f"✅ CFOPs de transferência conferidos por {confirmacao['confirmado_por_email'] or '—'} "
                f"em {confirmacao['confirmado_em']:%d/%m/%Y %H:%M}."
            )
        else:
            st.error(
                f"❌ Os CFOPs de transferência abaixo foram marcados como **incorretos** por "
                f"{confirmacao['confirmado_por_email'] or '—'} em "
                f"{confirmacao['confirmado_em']:%d/%m/%Y %H:%M} — reimporte a Rotina 1024 corrigida acima "
                f"(a reimportação já pede uma nova conferência automaticamente)."
            )
            st.dataframe(
                pd.DataFrame(cfops_transf).assign(
                    valor_contabil=lambda d: d["valor_contabil"].apply(formatar_moeda)
                )[["direcao", "cfop", "descricao", "valor_contabil"]],
                use_container_width=True,
            )

# ============================================================================================
with aba_extrato:
    st.caption(
        "Extrato de Notas Fiscais Relativas a Operações Interestaduais Sujeitas ao ICMS Antecipado "
        "(e-Fisco/PE) — usa o quadro \"Resumo do Grupo de Mercadorias para Extrato dos itens COBRADOS\". "
        "A linha 3.2 da Apuração (Antecipação fora do estado) soma o \"ICMS Devido\" só dos grupos com "
        "\"Direito a Crédito\" = Sim."
    )
    c_up1, c_up2 = st.columns([3, 1])
    pdf_extrato = c_up1.file_uploader("PDF do Extrato de ICMS Antecipado", type=["pdf"],
                                       key="upload_extrato_pe", label_visibility="collapsed")
    if c_up2.button("📥 Importar do PDF", key="importar_extrato_pe", disabled=pdf_extrato is None):
        try:
            cid = _garantir_competencia()
            grupos = parse_extrato_antecipado(pdf_extrato)
            n = salvar_extrato_antecipado(session, cid, grupos)
            st.success(f"{n} grupo(s) de mercadoria importado(s) do Extrato.")
            st.rerun()
        except ValueError as e:
            st.error(str(e))

    df_extrato = listar_extrato_antecipado(session, cid)
    if df_extrato.empty:
        st.info("Nenhum Extrato de ICMS Antecipado importado ainda para esta competência.")
    else:
        st.dataframe(
            df_extrato.assign(icms_devido=df_extrato["icms_devido"].apply(formatar_moeda)),
            use_container_width=True,
        )
        total = df_extrato.loc[df_extrato["direito_credito"], "icms_devido"].sum()
        st.metric("Total com Direito a Crédito (= linha 3.2)", formatar_moeda(total))

# ============================================================================================
with aba_nao_recuperavel:
    st.markdown(
        "**Para que serve esta aba:** valores do Extrato de ICMS Antecipado (e-Fisco/PE) marcados com "
        "\"Direito a Crédito\" = **Não** — são grupos de mercadoria em que o ICMS Antecipado foi pago mas "
        "**não gera crédito nenhum** (é imposto pago e perdido). Por isso NÃO entram na linha 3.2 nem em "
        "nenhuma outra linha da Apuração ICMS PE, e também não aparecem na Rotina 1025 — ficam só aqui, "
        "separados, pra dar visibilidade e permitir conferir se vale revisar a classificação do grupo junto "
        "à Sefaz/PE. Pedido do usuário em 10/08/2026."
    )
    df_nao_recuperavel = listar_nao_recuperavel(session, cid)
    if df_nao_recuperavel.empty:
        st.info("Nenhum grupo sem direito a crédito nesta competência (ou o Extrato ainda não foi "
                "importado — veja a aba \"Extrato de ICMS Antecipado\").")
    else:
        st.dataframe(
            df_nao_recuperavel.assign(icms_devido=df_nao_recuperavel["icms_devido"].apply(formatar_moeda)),
            use_container_width=True,
        )
        total_nao_recuperavel = df_nao_recuperavel["icms_devido"].sum()
        st.metric("Total não recuperável (fora da apuração)", formatar_moeda(total_nao_recuperavel))

# ============================================================================================
with aba_cfops:
    st.markdown(
        "**Para que serve esta aba:** cadastro dos CFOPs de Entrada que compõem a base da Antecipação, "
        "por empresa — \"interna\" soma na linha 3.1 (calculada a 1,1% do total), \"externa\" soma na "
        "linha 3.2.1 (valor de referência/auditoria; o valor real da 3.2 vem do Extrato do e-Fisco). Um "
        "cadastro inicial já vem pré-carregado a partir da planilha de apuração real, mas pode editar."
    )
    cfops_df = listar_cfops_antecipacao(session, empresa_id)
    st.caption(f"{len(cfops_df)} CFOP(s) cadastrado(s) para {empresa['razao_social']}.")
    cfops_editado = st.data_editor(
        cfops_df, use_container_width=True, num_rows="dynamic", key="editor_cfops_antecipacao_pe",
        column_config={
            "id": st.column_config.NumberColumn("ID", disabled=True),
            "cfop": st.column_config.NumberColumn("CFOP", required=True),
            "descricao": st.column_config.TextColumn("Descrição", disabled=True, width="large"),
            "bucket": st.column_config.SelectboxColumn("Bucket", options=["interna", "externa"], required=True),
            "observacao": st.column_config.TextColumn("Observação (opcional)", width="large"),
            "criado_por_email": st.column_config.TextColumn("Cadastrado por", disabled=True),
            "created_at": st.column_config.DatetimeColumn("Cadastrado em", disabled=True),
        },
        column_order=["cfop", "descricao", "bucket", "observacao", "criado_por_email", "created_at", "id"],
    )
    st.caption("Para incluir: adicione uma linha nova (ícone + no final da grade), digite o CFOP e escolha "
               "o bucket. Para excluir: selecione a linha e apague (ícone de lixeira). Depois clique em Salvar.")
    if st.button("💾 Salvar CFOPs de Antecipação"):
        resultado = salvar_cfops_antecipacao(session, empresa_id, cfops_df, cfops_editado, usuario=usuario_atual())
        st.success(f"{resultado['incluidos']} incluído(s), {resultado['removidos']} removido(s).")
        st.rerun()

# ============================================================================================
with aba_apuracao:
    st.markdown("#### Linha 4.1.01 — Valor Total das Saídas ajustado")
    st.caption(
        "Única linha da apuração que não dá para calcular com 100% de certeza só com os dados da Rotina "
        "1024: é o total de Valores Contábeis das Saídas, menos 'outras saídas' e devoluções de compra, "
        "menos uma eventual reclassificação contábil que não aparece em nenhum CFOP do relatório. Por "
        "isso é um campo editável — a sugestão abaixo é só um ponto de partida, confira/ajuste contra seu "
        "controle interno antes de calcular."
    )
    try:
        sugestao = sugerir_valor_4101(session, cid)
    except Exception:
        sugestao = None
    valor_salvo = carregar_valor_4101_manual(session, cid)
    valor_inicial = float(valor_salvo) if valor_salvo is not None else (float(sugestao) if sugestao else 0.0)
    if sugestao is not None:
        st.caption(f"Sugestão calculada (sem a reclassificação manual): {formatar_moeda(sugestao)}.")
    c_41, c_42 = st.columns([2, 1])
    valor_4101 = c_41.number_input("Valor da linha 4.1.01 (R$)", value=valor_inicial, step=0.01, format="%.2f")
    if c_42.button("💾 Salvar valor manual"):
        cid = _garantir_competencia()
        salvar_valor_4101_manual(session, cid, valor_4101)
        st.success("Valor salvo.")
        st.rerun()

    st.markdown("---")
    st.markdown("#### Linhas 1.2/1.3 — Créditos de antecipação do mês anterior")
    st.caption(
        "Por padrão, essas duas linhas são encadeadas automaticamente a partir da apuração já calculada da "
        "competência anterior desta mesma empresa (linhas 3.1 e 3.2 de lá). Use os campos abaixo só se "
        "precisar sobrescrever — por exemplo, na primeira competência cadastrada no sistema pra essa "
        "empresa (não existe 'mês anterior' aqui dentro pra encadear, mas o valor recolhido de fato existe "
        "fora do sistema)."
    )
    comp_ant_id = competencia_anterior_id(session, empresa_id, int(ano), int(mes))
    if comp_ant_id:
        sugestao_1_2 = session.execute(text(
            "select valor from apuracao_linhas where competencia_id = :cid and linha = '3.1'"
        ), {"cid": comp_ant_id}).scalar() or 0
        sugestao_1_3 = session.execute(text(
            "select valor from apuracao_linhas where competencia_id = :cid and linha = '3.2'"
        ), {"cid": comp_ant_id}).scalar() or 0
        st.caption(f"Encadeado automaticamente da competência anterior: 1.2 = {formatar_moeda(sugestao_1_2)}, "
                   f"1.3 = {formatar_moeda(sugestao_1_3)}.")
    else:
        sugestao_1_2 = sugestao_1_3 = 0
        st.caption("Não há competência anterior calculada no sistema para essa empresa — sem override "
                   "manual, essas linhas ficam em R$ 0,00.")

    manual_1_2 = carregar_valor_manual_pe(session, cid, "1.2")
    manual_1_3 = carregar_valor_manual_pe(session, cid, "1.3")

    c_12a, c_12b, c_12c = st.columns([2, 1, 1])
    valor_1_2 = c_12a.number_input(
        "Valor manual da linha 1.2 (R$)", value=float(manual_1_2) if manual_1_2 is not None else float(sugestao_1_2),
        step=0.01, format="%.2f", key="valor_1_2_manual",
    )
    if c_12b.button("💾 Salvar 1.2", key="salvar_1_2_manual"):
        cid = _garantir_competencia()
        salvar_valor_manual_pe(session, cid, "1.2", valor_1_2)
        st.success("Valor da linha 1.2 salvo — vai sobrescrever o encadeamento automático.")
        st.rerun()
    if c_12c.button("↩️ Usar automático", key="remover_1_2_manual", disabled=manual_1_2 is None):
        remover_valor_manual_pe(session, cid, "1.2")
        st.success("Override removido — linha 1.2 volta a ser encadeada automaticamente.")
        st.rerun()

    c_13a, c_13b, c_13c = st.columns([2, 1, 1])
    valor_1_3 = c_13a.number_input(
        "Valor manual da linha 1.3 (R$)", value=float(manual_1_3) if manual_1_3 is not None else float(sugestao_1_3),
        step=0.01, format="%.2f", key="valor_1_3_manual",
    )
    if c_13b.button("💾 Salvar 1.3", key="salvar_1_3_manual"):
        cid = _garantir_competencia()
        salvar_valor_manual_pe(session, cid, "1.3", valor_1_3)
        st.success("Valor da linha 1.3 salvo — vai sobrescrever o encadeamento automático.")
        st.rerun()
    if c_13c.button("↩️ Usar automático", key="remover_1_3_manual", disabled=manual_1_3 is None):
        remover_valor_manual_pe(session, cid, "1.3")
        st.success("Override removido — linha 1.3 volta a ser encadeada automaticamente.")
        st.rerun()

    st.markdown("---")
    # Trava o cálculo se houver CFOP de transferência pendente de conferência (aba Rotina 1024) — pedido
    # do usuário em 10/08/2026, ver listar_cfops_transferencia_checkpoint/carregar_confirmacao.
    _cfops_transf_pendente = listar_cfops_transferencia_checkpoint(session, cid)
    _confirmacao_transf = carregar_confirmacao(session, cid, "cfop_transferencia_pe") if _cfops_transf_pendente else None
    _bloqueado_transf = bool(_cfops_transf_pendente) and not (_confirmacao_transf and _confirmacao_transf["confirmado"])
    if _bloqueado_transf:
        st.warning(
            "⚠️ Há CFOP(s) de transferência pendente(s) de conferência (aba **📥 Rotina 1024**) — confirme "
            "se estão corretos antes de calcular a apuração."
        )
    if st.button("🧮 Calcular apuração", type="primary", disabled=_bloqueado_transf):
        cid = _garantir_competencia()
        with st.spinner("Calculando..."):
            linhas = calcular_apuracao_pe(session, cid, empresa_id, int(ano), int(mes),
                                           valor_4101_manual=valor_4101)
            salvar_apuracao(session, cid, linhas)
            session.execute(text("update competencias set status = 'calculada' where id = :cid"), {"cid": cid})
            session.commit()
        st.success("Calculado.")
        st.rerun()

    linhas_db = {r["linha"]: r for r in session.execute(text("""
        select linha, descricao, valor, detalhe from apuracao_linhas where competencia_id = :cid
    """), {"cid": cid}).mappings().all()}

    if not linhas_db:
        st.info("Ainda não calculado.")
    else:
        def _linha(cod):
            r = linhas_db.get(cod)
            return r["valor"] if r else 0

        def _tabela(pares):
            return pd.DataFrame([
                {"Linha": cod, "Descrição": desc, "Valor": formatar_moeda(_linha(cod))}
                for cod, desc in pares
            ]).set_index("Linha")

        st.markdown("#### 3. ANTECIPAÇÃO")
        st.table(_tabela([
            ("3", "Antecipação (3.1 + 3.2)"),
            ("3.1", "Antecipação 1,1% dentro do estado"),
            ("3.1.1", "Total base Entrada/Antecipação interna"),
            ("3.2", "Antecipação fora do estado (Extrato e-Fisco)"),
            ("3.2.1", "Total base Entrada externa (referência)"),
        ]))

        st.markdown("#### 1. CRÉDITOS TOTAIS")
        st.table(_tabela([
            ("1", "Créditos Totais"),
            ("1.1", "Créditos Entradas - Devoluções"),
            ("1.2", "Crédito 1,1% recolhido no mês anterior"),
            ("1.3", "Crédito 6% recolhido no mês anterior"),
            ("1.4", "Estorno de débitos - Devoluções de vendas (não-ST)"),
            ("1.5", "Estorno de débitos - Devoluções de vendas ST (Outros)"),
        ]))

        st.markdown("#### 2. DÉBITOS TOTAIS")
        st.table(_tabela([
            ("2", "Débitos Totais"),
            ("2.1", "Débito Saídas"),
            ("2.2", "Estorno de crédito - Devolução de compras"),
            ("2.3", "Débito Transferências"),
        ]))

        st.markdown("#### 4. CRÉDITO PRESUMIDO")
        st.table(_tabela([
            ("4", "Crédito Presumido (4.1 x 4.2 - 4.3)"),
            ("4.1", "Alíquota Média (4.1.02/4.1.01)"),
            ("4.1.01", "Valor Total das Saídas ajustado"),
            ("4.1.02", "Valor Total dos débitos"),
            ("4.2", "Base de cálculo Crédito Presumido"),
            ("4.2.01", "Aquisições - Devoluções - Serviços - Remessas"),
            ("4.2.02", "Adicional 35% Aquisições"),
            ("4.3", "Deduções Demais Créditos"),
            ("4.3.01", "Antecipação"),
            ("4.3.02", "Crédito Entradas"),
        ]))

        st.markdown("#### 5/6/7. RECOLHIMENTO")
        st.table(_tabela([
            ("5", "Valor a Recolher (2. - 1. - 4.)"),
            ("6", "Saldo Crédito Anterior"),
            ("7", "Valor Recolher Atual (5. - 6.)"),
        ]))

        valor_7 = _linha("7")
        detalhe_7 = (linhas_db.get("7") or {}).get("detalhe") or {}
        aviso_semestre = detalhe_7.get("aviso_zerar_saldo_credor_semestre")
        if aviso_semestre:
            st.error(
                f"🔔 {aviso_semestre} Valor a zerar com o lançamento de \"Outros Débitos\": "
                f"{formatar_moeda(-valor_7)}."
            )
        elif valor_7 and valor_7 < 0:
            st.success(f"Saldo credor a transportar para o mês seguinte: {formatar_moeda(-valor_7)}.")
        elif valor_7:
            st.warning(f"Valor a recolher neste mês: {formatar_moeda(valor_7)}.")

        # ----------------------------------------------------------------------------------
        # Conferência com a Rotina 1025 (Livro Registro de Apuração do ICMS) — pedido do usuário em
        # 10/08/2026. Mapeamento das linhas próprias da Apuração PE para as linhas oficiais do livro
        # validado ao centavo contra um PDF real (ver docstring de comparar_com_checkpoint_1025_pe em
        # app/lib/calculo_icms_pe.py).
        # ----------------------------------------------------------------------------------
        st.markdown("---")
        with st.expander("📎 Conferência com a Rotina 1025 (Livro Registro de Apuração)", expanded=False):
            st.caption(
                "Anexe o PDF da Rotina 1025 (Livro Registro de Apuração do ICMS) desta competência e "
                "clique em Importar — preenche as 14 linhas automaticamente, sem digitar, e compara com o "
                "calculado acima. As linhas 01/02/03/07/12 do livro não têm um mapeamento confiável no "
                "regime de Crédito Presumido (aparecem só como referência, sem comparação)."
            )
            c_up1025_1, c_up1025_2 = st.columns([3, 1])
            pdf_1025_pe = c_up1025_1.file_uploader(
                "PDF da Rotina 1025", type=["pdf"], key="upload_1025_pe", label_visibility="collapsed",
            )
            if c_up1025_2.button("📥 Importar do PDF", key="importar_1025_pe", disabled=pdf_1025_pe is None):
                try:
                    cid = _garantir_competencia()
                    valores_1025 = parse_rotina_1025(pdf_1025_pe)
                    df_1025_pe = pd.DataFrame([
                        {"linha": linha, "valor_1025": float(valor)}
                        for linha, valor in valores_1025.items()
                    ])
                    n = salvar_checkpoint_1025_bulk(session, cid, df_1025_pe)
                    st.success(f"{n} linha(s) importada(s) do PDF da Rotina 1025.")
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))

            comparacao_1025 = comparar_com_checkpoint_1025_pe(session, cid)
            if not comparacao_1025:
                st.info("Ainda não há valores da Rotina 1025 importados/digitados para comparar.")
            else:
                divergencias_1025 = [c for c in comparacao_1025 if c["mapeado"] and c["diff"] is not None
                                      and abs(c["diff"]) > 0.05]
                df_comp = pd.DataFrame([
                    {
                        "Linha": c["linha"], "Descrição": c["descricao"],
                        "Calculado (PE)": formatar_moeda(c["valor_calc"]) if c["mapeado"] else "—",
                        "Rotina 1025": formatar_moeda(c["valor_ref"]) if c["valor_ref"] is not None else "—",
                        "Diferença": formatar_moeda(c["diff"]) if c["diff"] is not None else "—",
                    }
                    for c in comparacao_1025
                ]).set_index("Linha")
                st.table(df_comp)
                if divergencias_1025:
                    st.error(
                        f"⚠️ {len(divergencias_1025)} linha(s) divergente(s) (diferença > R$ 0,05) entre o "
                        f"calculado e a Rotina 1025 — confira."
                    )
                else:
                    st.success("Tudo bate com a Rotina 1025 (nas linhas com comparação automática).")
