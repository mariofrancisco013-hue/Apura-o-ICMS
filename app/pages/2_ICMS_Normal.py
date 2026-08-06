import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st
import pandas as pd
from lib.auth import require_login, logout_button, usuario_atual
from lib.db import get_session
from lib.planilha import (
    carregar_itens, salvar_itens_editados, resumo_por_cfop, carregar_totalizador,
    carregar_checkpoint_1024_editavel, salvar_checkpoint_1024_bulk,
    carregar_checkpoint_1025_editavel, salvar_checkpoint_1025_bulk, LABELS_INCONSISTENCIA,
    carregar_historico_edicoes,
)
from lib.calculo_icms_normal import calcular_apuracao_icms_normal, salvar_apuracao
from lib.validacoes import gerar_inconsistencias_ncm, gerar_inconsistencias_transferencia
from lib.ncm_tributado import (
    listar_ncms_tributados, salvar_ncms_tributados, gerar_inconsistencias_ncm_tributado,
)
from lib.importar_1024 import parse_rotina_1024
from lib.formatacao import formatar_moeda, coluna_moeda
from lib.status_apuracao import status_competencia
from sqlalchemy import text

st.set_page_config(page_title="ICMS Normal", layout="wide")
require_login()
logout_button()
st.title("ICMS Normal")

session = get_session()
competencias = session.execute(text("""
    select c.id, e.id as empresa_id, e.razao_social, c.ano, c.mes, c.status
    from competencias c join empresas e on e.id = c.empresa_id
    where c.modulo = 'icms_normal'
    order by c.ano desc, c.mes desc
""")).mappings().all()

if not competencias:
    st.info("Nenhuma competência importada ainda. Vá em **Importar Relatórios** primeiro.")
    st.stop()

comp = st.selectbox(
    "Competência", competencias,
    format_func=lambda c: f"{c['razao_social']} — {c['mes']:02d}/{c['ano']} ({c['status']})",
)
cid = comp["id"]
empresa_id = comp["empresa_id"]

_status = status_competencia(session, cid, comp["status"])
{"success": st.success, "warning": st.warning, "info": st.info}[_status["nivel"]](_status["texto"])

aba_entrada, aba_saida, aba_ncm, aba_ajustes, aba_apuracao, aba_inconsistencias = st.tabs([
    "📥 Planilha de Entrada", "📤 Planilha de Saída", "🔖 NCMs Tributados",
    "🧮 Ajustes na Apuração", "📋 Apuração", "⚠️ Inconsistências",
])


def _formatar_moeda_df(df, colunas):
    """Devolve uma cópia do DataFrame com as colunas indicadas formatadas como texto 'R$ 1.234,56' — só
    para exibição (st.dataframe/st.table), nunca para grades editáveis (ali a coluna precisa continuar
    numérica, ver lib/formatacao.py)."""
    if df.empty:
        return df
    out = df.copy()
    for c in colunas:
        if c in out.columns:
            out[c] = out[c].apply(formatar_moeda)
    return out


def _aba_planilha(tipo_operacao, titulo):
    st.caption(
        "Ajuste diretamente na grade (igual planilha) se algum CFOP, NCM ou valor estiver errado no "
        "relatório original. As mudanças só valem para esta competência e não alteram o arquivo .xls. Ao "
        "salvar, as ⚠️ Inconsistências desta competência são recalculadas na hora — se o ajuste corrigiu o "
        "problema, o alerta some sozinho, sem precisar ir na aba Apuração clicar em 'Calcular apuração' de "
        "novo (mas os VALORES da apuração, linhas 01-14, aí sim só atualizam quando você clicar lá)."
    )

    visao = st.radio(
        "Visão", ["Analítica (item a item)", "Sintética (totalizada por UF, Código do Produto e Alíquota)"],
        horizontal=True, key=f"visao_{tipo_operacao}",
        help="Sintética soma os itens por UF + Código do Produto + Alíquota de ICMS — útil para conferir "
             "volumes sem rolar item a item. Analítica mostra e permite editar nota a nota.",
    )
    sintetica = visao.startswith("Sintética")

    c1, c_ncm, c2, c3 = st.columns([2, 2, 3, 2])
    resumo = resumo_por_cfop(session, cid, tipo_operacao)
    cfops_disponiveis = ["(todos)"] + resumo["cfop"].tolist() if not resumo.empty else ["(todos)"]
    cfop_sel = c1.selectbox("Filtrar por CFOP", cfops_disponiveis, key=f"cfop_{tipo_operacao}")
    cfop_filtro = None if cfop_sel == "(todos)" else int(cfop_sel)
    ncm_filtro = c_ncm.text_input(
        "Filtrar por NCM", key=f"ncm_{tipo_operacao}", placeholder="ex: 8213 ou 82130000",
        help="Filtra por prefixo — '8213' pega qualquer NCM que comece com 8213 (o capítulo inteiro), "
             "não só o código exato.",
    )

    if sintetica:
        limite = c3.number_input("Máx. linhas na tela", min_value=50, max_value=5000, value=500, step=50,
                                  key=f"limite_{tipo_operacao}")
        tot = carregar_totalizador(session, cid, tipo_operacao, cfop_filtro, ncm_filtro or None)
        st.caption(f"{len(tot)} combinação(ões) de UF + Código do Produto + Alíquota"
                   f"{' para este CFOP/NCM' if (cfop_filtro or ncm_filtro) else ''}.")
        st.dataframe(
            _formatar_moeda_df(tot.head(limite), ["valor_produto", "base_icms", "valor_icms"]),
            use_container_width=True, height=420,
            column_config={
                "uf": st.column_config.TextColumn("UF"),
                "produto_codigo": st.column_config.TextColumn("Código Produto"),
                "produto_descricao": st.column_config.TextColumn("Descrição Produto", width="large"),
                "aliq_icms": st.column_config.NumberColumn("Alíquota ICMS %", format="%.2f"),
                "n_itens": st.column_config.NumberColumn("Nº itens"),
                "valor_produto": st.column_config.TextColumn("Valor Produto"),
                "base_icms": st.column_config.TextColumn("Base ICMS"),
                "valor_icms": st.column_config.TextColumn("Valor ICMS"),
            },
        )
    else:
        busca = c2.text_input("Buscar por NF, código/descrição do produto ou parceiro",
                               key=f"busca_{tipo_operacao}")
        limite = c3.number_input("Máx. linhas na tela", min_value=50, max_value=5000, value=500, step=50,
                                  key=f"limite_{tipo_operacao}")
        tipos_inc_sel = st.multiselect(
            "⚠️ Filtrar por tipo de inconsistência pendente",
            options=list(LABELS_INCONSISTENCIA.keys()),
            format_func=lambda t: LABELS_INCONSISTENCIA[t],
            key=f"tipos_inc_{tipo_operacao}",
            help="Deixe vazio pra mostrar todos os itens. Escolha um ou mais tipos pra ver só os itens com "
                 "aquele erro específico pendente — os mesmos tipos da aba Inconsistências (que tem a "
                 "descrição completa de cada um)."
        )

        df, total = carregar_itens(session, cid, tipo_operacao, empresa_id, cfop_filtro, busca or None,
                                    limite, tipos_inconsistencia=tipos_inc_sel or None,
                                    ncm_filtro=ncm_filtro or None)

        if total > len(df):
            st.warning(f"Mostrando {len(df)} de {total} itens (use o filtro de CFOP ou busca para refinar, "
                       f"ou aumente o limite acima — grades muito grandes deixam o navegador lento).")
        else:
            st.caption(f"{total} itens.")

        editado = st.data_editor(
            df, use_container_width=True, height=420, num_rows="fixed", key=f"editor_{tipo_operacao}",
            column_order=["id", "inconsistencia", "nf_numero", "parceiro", "produto_codigo",
                          "produto_descricao", "ncm", "ncm_descricao", "cfop", "valor_produto", "aliq_icms",
                          "base_icms", "valor_icms", "uf"],
            column_config={
                "id": st.column_config.NumberColumn("ID", disabled=True),
                "inconsistencia": st.column_config.TextColumn(
                    "⚠️ Inconsistência", disabled=True, width="medium",
                    help="Sinaliza inconsistência(s) PENDENTE(s) ligada(s) a este item, geradas ao "
                         "calcular a apuração. Em branco não é garantia de que está tudo certo — só que "
                         "nenhuma das 4 validações automáticas pegou nada nesta linha. Descrição completa "
                         "e opção de marcar como revisado/ignorar: aba Inconsistências."
                ),
                "produto_codigo": st.column_config.TextColumn("Código Produto"),
                "produto_descricao": st.column_config.TextColumn("Descrição Produto", width="large"),
                "ncm_descricao": st.column_config.TextColumn(
                    "NCM tributado — o quê", disabled=True, width="large",
                    help="Só preenche quando o NCM está cadastrado na aba 'NCMs Tributados' desta empresa "
                         "— mostra a descrição oficial da Tabela NCM. Em branco não quer dizer que o NCM "
                         "não existe, só que ele ainda não está nessa lista. Não é gravada, é só consulta."
                ),
                "valor_produto": coluna_moeda("Valor Produto"),
                "base_icms": coluna_moeda("Base ICMS"),
                "valor_icms": coluna_moeda("Valor ICMS"),
            },
        )
        if st.button("💾 Salvar alterações", key=f"salvar_{tipo_operacao}"):
            n = salvar_itens_editados(session, df, editado, competencia_id=cid, tipo_operacao=tipo_operacao,
                                       usuario=usuario_atual())
            if n:
                with st.spinner("Recalculando inconsistências..."):
                    n_ncm = gerar_inconsistencias_ncm(session, cid, empresa_id)
                    n_transf = gerar_inconsistencias_transferencia(session, cid, empresa_id)
                    n_ncm_trib = gerar_inconsistencias_ncm_tributado(session, cid, empresa_id)
                st.success(
                    f"{n} linha(s) atualizada(s). Inconsistências recalculadas: {n_ncm} de NCM×ST, "
                    f"{n_transf} de transferência, {n_ncm_trib} de NCM tributado — o que foi corrigido já "
                    f"some da aba ⚠️ Inconsistências e da coluna de alerta aqui na grade."
                )
            else:
                st.info("Nenhuma mudança detectada.")
            st.rerun()

    with st.expander("📝 Histórico de ajustes manuais desta planilha (mais recentes primeiro)"):
        hist = carregar_historico_edicoes(session, cid, tipo_operacao)
        if hist.empty:
            st.caption("Nenhum ajuste manual registrado ainda nesta planilha, para esta competência.")
        else:
            st.dataframe(
                hist, use_container_width=True, height=300,
                column_order=["nf_item_id", "nf_numero", "campo", "valor_anterior", "valor_novo",
                              "editado_por_email", "editado_em"],
                column_config={
                    "nf_item_id": st.column_config.NumberColumn("ID Item"),
                    "nf_numero": st.column_config.TextColumn("NF"),
                    "campo": st.column_config.TextColumn("Campo alterado"),
                    "valor_anterior": st.column_config.TextColumn("Valor anterior"),
                    "valor_novo": st.column_config.TextColumn("Valor novo"),
                    "editado_por_email": st.column_config.TextColumn("Editado por"),
                    "editado_em": st.column_config.DatetimeColumn("Quando", format="DD/MM/YYYY HH:mm"),
                },
            )

    st.markdown("---")
    st.subheader("Resumo por CFOP")
    st.dataframe(_formatar_moeda_df(resumo, ["base", "icms"]), use_container_width=True)

    st.markdown("---")
    with st.expander(f"📎 Conferência com a Rotina 1024 ({'Entrada' if tipo_operacao=='entrada' else 'Saída'})"):
        st.caption(
            "Anexe o PDF da Rotina 1024 (Livro RAICMS Modelo P9) e clique em Importar — preenche as "
            "colunas 'base_1024'/'icms_1024' de todos os CFOPs automaticamente, sem digitar. Se preferir, "
            "também dá para editar/colar valor a valor direto na grade abaixo. Mostra TODO CFOP que "
            "aparecer no relatório importado OU na Rotina 1024, mesmo que só num dos dois — é assim que "
            "aparecem CFOPs como o 1602 (lançado direto no sistema contábil, sem passar por nota fiscal)."
        )
        c_up1, c_up2 = st.columns([3, 1])
        pdf_1024 = c_up1.file_uploader(
            "PDF da Rotina 1024", type=["pdf"], key=f"upload_1024_{tipo_operacao}",
            label_visibility="collapsed",
        )
        if c_up2.button("📥 Importar do PDF", key=f"importar_1024_{tipo_operacao}", disabled=pdf_1024 is None):
            try:
                linhas_1024 = parse_rotina_1024(pdf_1024)
                df_1024 = pd.DataFrame(linhas_1024).rename(
                    columns={"valor_base": "base_1024", "valor_icms": "icms_1024"}
                )
                n = salvar_checkpoint_1024_bulk(session, cid, df_1024)
                st.success(f"{n} CFOP(s) importado(s) do PDF da Rotina 1024 (Entrada + Saída juntas).")
                st.rerun()
            except ValueError as e:
                st.error(str(e))

        ref = carregar_checkpoint_1024_editavel(session, cid, tipo_operacao)
        ref_editado = st.data_editor(
            ref, use_container_width=True, key=f"checkpoint1024_{tipo_operacao}",
            column_config={
                "cfop": st.column_config.NumberColumn("CFOP", disabled=True),
                "descricao": st.column_config.TextColumn("Descrição", disabled=True),
                "base_calc": coluna_moeda("Base (calculado)", disabled=True),
                "icms_calc": coluna_moeda("ICMS (calculado)", disabled=True),
                "base_1024": coluna_moeda("Base (Rotina 1024)"),
                "icms_1024": coluna_moeda("ICMS (Rotina 1024)"),
            },
        )
        if st.button("Salvar valores da Rotina 1024", key=f"salvar_1024_{tipo_operacao}"):
            n = salvar_checkpoint_1024_bulk(session, cid, ref_editado)
            st.success(f"{n} CFOP(s) salvo(s).")
            st.rerun()

        diffs = ref_editado.assign(
            diff_base=ref_editado["base_calc"] - ref_editado["base_1024"],
            diff_icms=ref_editado["icms_calc"] - ref_editado["icms_1024"],
        )
        divergentes = diffs[(diffs["diff_base"].abs() > 0.05) | (diffs["diff_icms"].abs() > 0.05)]
        if not divergentes.empty:
            st.warning(f"{len(divergentes)} CFOP(s) com diferença acima de R$ 0,05:")
            st.dataframe(
                _formatar_moeda_df(divergentes[["cfop", "descricao", "diff_base", "diff_icms"]],
                                    ["diff_base", "diff_icms"]),
                use_container_width=True,
            )
        elif ref_editado["base_1024"].notna().any():
            st.success("Tudo bateu com os valores da Rotina 1024 informados.")


with aba_entrada:
    _aba_planilha("entrada", "Entrada")

with aba_saida:
    _aba_planilha("saida", "Saída")

with aba_ncm:
    st.markdown(
        "**Para que serve esta aba:** cadastro dos NCMs que você sabe que são \"de fato tributados\" "
        "(não-ST — geram crédito/débito pleno de ICMS), esperados nos CFOPs 1102, 1202, 5102, 6102 e "
        "5927. É por empresa porque o mesmo NCM pode ter tratamento diferente dependendo da empresa/UF. "
        "A partir desse cadastro, ao clicar em **Calcular apuração** (aba Apuração) o sistema sinaliza "
        "duas coisas, sempre para você revisar — ele nunca decide sozinho:"
    )
    st.markdown(
        "- Um NCM que **está** cadastrado aqui mas apareceu num item classificado como **ST** — pode ser "
        "erro de CFOP no lançamento, ou o produto pode ter deixado de ser tributado.\n"
        "- Um NCM que **não está** cadastrado mas apareceu como **tributado (não-ST)** — um candidato "
        "novo para você confirmar se deve entrar na lista."
    )
    st.caption(
        "Essas sinalizações aparecem na aba **⚠️ Inconsistências**, aqui mesmo (tipos 'ncm_tributado_como_st' e "
        "'ncm_tributado_novo')."
    )

    ncms_df = listar_ncms_tributados(session, empresa_id)
    st.caption(f"{len(ncms_df)} NCM(s) cadastrado(s) para {comp['razao_social']}. A coluna \"Descrição "
               "oficial\" vem da Tabela NCM da Receita Federal automaticamente — só digite algo em "
               "\"Observação\" se quiser anotar algo próprio.")
    ncms_editado = st.data_editor(
        ncms_df, use_container_width=True, num_rows="dynamic", key="editor_ncms_tributados",
        column_config={
            "id": st.column_config.NumberColumn("ID", disabled=True),
            "ncm": st.column_config.TextColumn("NCM", required=True),
            "descricao_oficial": st.column_config.TextColumn(
                "Descrição oficial (Tabela NCM)", disabled=True, width="large"
            ),
            "descricao": st.column_config.TextColumn("Observação (opcional)"),
            "created_at": st.column_config.DatetimeColumn("Cadastrado em", disabled=True),
        },
        column_order=["ncm", "descricao_oficial", "descricao", "created_at", "id"],
    )
    st.caption("Para incluir: adicione uma linha nova (ícone + no final da grade) e digite o NCM. "
               "Para excluir: selecione a linha e apague (ícone de lixeira). Depois clique em Salvar.")
    if st.button("💾 Salvar lista de NCMs tributados"):
        resultado = salvar_ncms_tributados(session, empresa_id, ncms_df, ncms_editado)
        st.success(f"{resultado['incluidos']} incluído(s), {resultado['removidos']} removido(s).")
        st.rerun()

with aba_ajustes:
    st.markdown(
        "**Direcionador de ajustes manuais desta competência.** Cada lançamento aqui é uma informação que "
        "NÃO veio pronta dos relatórios de Entrada/Saída e precisou ser incluída à mão para a apuração "
        "bater com o livro fiscal oficial — DIFAL (débito), CIAP e DAE Antecipado (crédito), e CFOPs que o "
        "sistema contábil lança direto, sem passar por nota fiscal (ex: CFOP 1602). Se o mesmo tipo de "
        "ajuste se repetir todo mês, é um sinal de que vale a pena corrigir na origem (Winthor) para não "
        "precisar mais lançar manualmente aqui."
    )
    lancamentos = session.execute(text("""
        select id, tipo, cfop_relacionado, descricao, valor from lancamentos_manuais
        where competencia_id = :cid order by tipo, id
    """), {"cid": cid}).mappings().all()

    col_deb, col_cred = st.columns(2)
    with col_deb:
        st.subheader("Débitos")
        deb = [dict(l, valor=formatar_moeda(l["valor"])) for l in lancamentos
               if l["tipo"] in ("difal_debito", "ajuste_cfop_debito")]
        st.dataframe(deb, use_container_width=True)
    with col_cred:
        st.subheader("Créditos")
        cred = [dict(l, valor=formatar_moeda(l["valor"])) for l in lancamentos
                if l["tipo"] in ("ciap_credito", "dae_antecipado_credito", "ajuste_cfop_credito")]
        st.dataframe(cred, use_container_width=True)

    with st.form("novo_lancamento", clear_on_submit=True):
        st.markdown("**Adicionar lançamento**")
        c1, c2, c3, c4 = st.columns([2, 3, 2, 2])
        tipo = c1.selectbox("Tipo", [
            "difal_debito", "ciap_credito", "dae_antecipado_credito",
            "ajuste_cfop_credito", "ajuste_cfop_debito", "outro",
        ], help=(
            "difal_debito: DIFAL a recolher. ciap_credito: crédito do CIAP do mês (anexo/arquivo CIAP). "
            "dae_antecipado_credito: DAE de ICMS Antecipado pago no período. ajuste_cfop_credito/debito: "
            "CFOP lançado direto no contábil, fora do relatório de NF (ex: CFOP 1602)."
        ))
        descricao = c2.text_input("Descrição (ex: 'CIAP mês 07/2026', 'DAE 202642354711255', 'CFOP 1602')")
        valor = c3.number_input("Valor (R$)", min_value=0.0, step=0.01, format="%.2f")
        cfop_rel = c4.number_input("CFOP relacionado (se aplicável)", min_value=0, max_value=6999, step=1)
        if st.form_submit_button("Adicionar"):
            session.execute(text("""
                insert into lancamentos_manuais (competencia_id, tipo, cfop_relacionado, descricao, valor)
                values (:cid, :tipo, :cfop, :desc, :valor)
            """), {"cid": cid, "tipo": tipo, "cfop": cfop_rel or None, "desc": descricao, "valor": valor})
            session.commit()
            st.rerun()

with aba_apuracao:
    st.caption(
        "Calcula as linhas 01-14 a partir da Planilha de Entrada/Saída (já com os ajustes que você fez) + "
        "os lançamentos manuais da aba Ajustes. O resultado abaixo é organizado como a Rotina 1025."
    )
    if st.button("🧮 Calcular apuração", type="primary"):
        with st.spinner("Calculando..."):
            linhas = calcular_apuracao_icms_normal(session, cid)
            salvar_apuracao(session, cid, linhas)
            n_ncm = gerar_inconsistencias_ncm(session, cid, empresa_id)
            n_transf = gerar_inconsistencias_transferencia(session, cid, empresa_id)
            n_ncm_trib = gerar_inconsistencias_ncm_tributado(session, cid, empresa_id)
            session.execute(text("update competencias set status = 'calculada' where id = :cid"), {"cid": cid})
            session.commit()
        st.success(
            f"Calculado. {n_ncm} inconsistência(s) de NCM x ST, {n_transf} de transferência e "
            f"{n_ncm_trib} de NCM tributado geradas — veja a aba **⚠️ Inconsistências** aqui do lado."
        )
        st.rerun()

    linhas_db = {r["linha"]: r for r in session.execute(text("""
        select linha, descricao, valor from apuracao_linhas where competencia_id = :cid
    """), {"cid": cid}).mappings().all()}

    if not linhas_db:
        st.info("Ainda não calculado.")
    else:
        def _linha(cod):
            r = linhas_db.get(cod)
            return r["valor"] if r else 0

        st.markdown("#### DÉBITO DO IMPOSTO")
        st.table(pd.DataFrame([
            {"Linha": "01", "Descrição": "Por Saídas com débito", "Valor": formatar_moeda(_linha('01'))},
            {"Linha": "02", "Descrição": "Outros Débitos", "Valor": formatar_moeda(_linha('02'))},
            {"Linha": "03", "Descrição": "Estorno de Créditos", "Valor": formatar_moeda(_linha('03'))},
            {"Linha": "04", "Descrição": "Subtotal Débito", "Valor": formatar_moeda(_linha('04'))},
        ]).set_index("Linha"))

        st.markdown("#### CRÉDITO DO IMPOSTO")
        st.table(pd.DataFrame([
            {"Linha": "05", "Descrição": "Por Entradas com crédito", "Valor": formatar_moeda(_linha('05'))},
            {"Linha": "06", "Descrição": "Outros Créditos", "Valor": formatar_moeda(_linha('06'))},
            {"Linha": "07", "Descrição": "Estorno de Débitos", "Valor": formatar_moeda(_linha('07'))},
            {"Linha": "08", "Descrição": "Subtotal Crédito", "Valor": formatar_moeda(_linha('08'))},
        ]).set_index("Linha"))

        st.markdown("#### APURAÇÃO DO SALDO")
        st.table(pd.DataFrame([
            {"Linha": "09", "Descrição": "Saldo Credor do Período Anterior", "Valor": formatar_moeda(_linha('09'))},
            {"Linha": "11", "Descrição": "Saldo Devedor (Débito menos Crédito)", "Valor": formatar_moeda(_linha('11'))},
            {"Linha": "12", "Descrição": "Deduções", "Valor": formatar_moeda(_linha('12'))},
            {"Linha": "13", "Descrição": "Imposto a Recolher", "Valor": formatar_moeda(_linha('13'))},
            {"Linha": "14", "Descrição": "Saldo Credor a Transportar", "Valor": formatar_moeda(_linha('14'))},
        ]).set_index("Linha"))

        st.markdown("---")
        with st.expander("📎 Conferência com a Rotina 1025 (livro completo)"):
            st.caption("Preencha 'valor_1025' com o valor de cada linha do livro fiscal oficial.")
            ref = carregar_checkpoint_1025_editavel(session, cid)
            ref_editado = st.data_editor(
                ref, use_container_width=True, key="checkpoint1025",
                column_config={
                    "linha": st.column_config.TextColumn("Linha", disabled=True),
                    "descricao": st.column_config.TextColumn("Descrição", disabled=True),
                    "valor_calc": coluna_moeda("Calculado", disabled=True),
                    "valor_1025": coluna_moeda("Rotina 1025"),
                },
            )
            if st.button("Salvar valores da Rotina 1025"):
                n = salvar_checkpoint_1025_bulk(session, cid, ref_editado)
                st.success(f"{n} linha(s) salva(s).")
                st.rerun()

            diffs = ref_editado.assign(diff=ref_editado["valor_calc"] - ref_editado["valor_1025"])
            divergentes = diffs[diffs["diff"].abs() > 0.05]
            if not divergentes.empty:
                st.warning(f"{len(divergentes)} linha(s) com diferença acima de R$ 0,05:")
                st.dataframe(
                    _formatar_moeda_df(divergentes[["linha", "descricao", "diff"]], ["diff"]),
                    use_container_width=True,
                )
            elif ref_editado["valor_1025"].notna().any():
                st.success("Apuração bate com a Rotina 1025.")

with aba_inconsistencias:
    st.caption(
        "NCM x ST: mesmo NCM tratado de forma diferente entre Entrada e Saída. Transferência não "
        "vinculada: CFOP de transferência cujo parceiro não bate por nome com nenhuma empresa do grupo "
        "cadastrada (heurística — o relatório de origem não traz o CNPJ do parceiro, confirme "
        "manualmente). NCM tributado como ST: um NCM cadastrado como 'tributado' na aba NCMs Tributados "
        "apareceu num item classificado como ST. NCM tributado novo: um NCM ainda não cadastrado apareceu "
        "como tributado (não-ST) — candidato a entrar na lista. Geradas ao clicar em 'Calcular apuração' "
        "na aba Apuração. Ocorrências do mesmo erro (mesmo NCM, ou mesmo parceiro+CFOP) aparecem "
        "AGRUPADAS numa linha só, com a quantidade de itens de NF por trás — revisar/ignorar/justificar o "
        "grupo já vale para todos os itens dele de uma vez. Cada item afetado também fica sinalizado "
        "direto na Planilha de Entrada/Saída (coluna ⚠️ Inconsistência)."
    )

    TIPOS_INCONSISTENCIA = [
        "ncm_st_inconsistente", "transferencia_nao_vinculada", "ncm_tributado_como_st", "ncm_tributado_novo",
    ]
    c_status, c_tipo = st.columns(2)
    status_filtro = c_status.multiselect("Status", ["pendente", "revisado", "ignorado"],
                                          default=["pendente"], key="inc_status")
    tipo_filtro = c_tipo.multiselect("Tipo", TIPOS_INCONSISTENCIA, default=TIPOS_INCONSISTENCIA, key="inc_tipo")

    if status_filtro and tipo_filtro:
        itens_inc = session.execute(text("""
            select id, tipo, ncm, cfop, descricao, status, revisado_por, revisado_em, quantidade,
                   chave_agrupamento, justificativa, aplicada_por_excecao
            from inconsistencias
            where competencia_id = :cid and status = any(:status) and tipo = any(:tipo)
            order by quantidade desc, created_at desc
        """), {"cid": cid, "status": status_filtro, "tipo": tipo_filtro}).mappings().all()

        total_itens_afetados = sum(item["quantidade"] or 1 for item in itens_inc)
        st.write(f"{len(itens_inc)} inconsistência(s) (grupo(s)) encontrada(s) — {total_itens_afetados} "
                 f"item(ns) de NF afetado(s) ao todo.")
        for item in itens_inc:
            qtd = item["quantidade"] or 1
            selo = f"{qtd}× " if qtd > 1 else ""
            marca_auto = " 🔁" if item["aplicada_por_excecao"] else ""
            with st.expander(f"{selo}[{item['tipo']}] {item['descricao'][:90]}...{marca_auto}"):
                st.write(item["descricao"])
                if qtd > 1:
                    st.caption(f"Esse mesmo erro se repete em {qtd} itens de NF nesta competência "
                               f"(agrupado numa linha só) — revisar/ignorar/justificar AQUI resolve os "
                               f"{qtd} itens de uma vez, todos eles somem do alerta na Planilha.")
                if item["aplicada_por_excecao"]:
                    st.info(f"🔁 Aplicado automaticamente — bateu com uma exceção conhecida cadastrada "
                            f"numa competência anterior. Justificativa: {item['justificativa']}")
                elif item["justificativa"]:
                    st.info(f"Justificativa: {item['justificativa']}")

                with st.form(f"form_inc_{item['id']}"):
                    justificativa = st.text_area(
                        "Justificativa (opcional, mas obrigatória se marcar 'replicar')",
                        value=item["justificativa"] or "", key=f"just_{item['id']}",
                    )
                    replicar = st.checkbox(
                        "🔁 Aplicar automaticamente nas próximas apurações desta empresa (não perguntar "
                        "de novo este mesmo caso — mesmo NCM/parceiro+CFOP)",
                        key=f"replicar_{item['id']}",
                        help="Cria uma regra: da próxima vez que este mesmo NCM (ou parceiro+CFOP) "
                             "aparecer numa apuração futura desta empresa, a inconsistência já nasce "
                             "revisada com esta justificativa, sem pedir revisão de novo.",
                    )
                    fc1, fc2, fc3 = st.columns(3)
                    revisar = fc1.form_submit_button("✅ Marcar como revisado")
                    ignorar = fc2.form_submit_button("🚫 Ignorar")
                    salvar_just = fc3.form_submit_button("💾 Só salvar justificativa")

                if revisar or ignorar or salvar_just:
                    if replicar and not justificativa.strip():
                        st.error("Pra replicar nas próximas apurações, escreve a justificativa primeiro.")
                    else:
                        novo_status = "revisado" if revisar else ("ignorado" if ignorar else item["status"])
                        usuario = usuario_atual()
                        session.execute(text("""
                            update inconsistencias
                            set status=:status, revisado_em=now(), revisado_por=:uid, justificativa=:just
                            where id=:id
                        """), {
                            "status": novo_status, "uid": usuario["id"], "just": justificativa.strip() or None,
                            "id": item["id"],
                        })
                        if replicar:
                            session.execute(text("""
                                insert into excecoes_inconsistencia
                                    (empresa_id, tipo, chave_agrupamento, ncm, cfop, justificativa,
                                     ativa, criado_por, criado_por_email)
                                values (:eid, :tipo, :chave, :ncm, :cfop, :just, true, :uid, :email)
                                on conflict (empresa_id, tipo, chave_agrupamento) do update
                                    set justificativa = excluded.justificativa, ativa = true,
                                        created_at = now(), criado_por = excluded.criado_por,
                                        criado_por_email = excluded.criado_por_email
                            """), {
                                "eid": empresa_id, "tipo": item["tipo"], "chave": item["chave_agrupamento"],
                                "ncm": item["ncm"], "cfop": item["cfop"], "just": justificativa.strip(),
                                "uid": usuario["id"], "email": usuario["email"],
                            })
                        session.commit()
                        st.rerun()

    st.markdown("---")
    with st.expander("🔁 Exceções conhecidas (regras aplicadas automaticamente nas próximas apurações)"):
        st.caption(
            "Quando você marca 'replicar nas próximas apurações' acima, a regra entra aqui. Desative se a "
            "situação mudar e você quiser voltar a ser avisado sobre esse mesmo caso."
        )
        excecoes = session.execute(text("""
            select id, tipo, ncm, cfop, chave_agrupamento, justificativa, ativa, criado_por_email, created_at
            from excecoes_inconsistencia
            where empresa_id = :eid
            order by ativa desc, created_at desc
        """), {"eid": empresa_id}).mappings().all()
        if not excecoes:
            st.caption("Nenhuma exceção cadastrada ainda para esta empresa.")
        for exc in excecoes:
            status_txt = "🟢 ativa" if exc["ativa"] else "⚪ desativada"
            with st.expander(f"[{exc['tipo']}] {exc['chave_agrupamento']} — {status_txt}"):
                st.write(exc["justificativa"])
                st.caption(f"Criada por {exc['criado_por_email'] or '?'} em {exc['created_at']}")
                if exc["ativa"]:
                    if st.button("Desativar (voltar a sinalizar este caso)", key=f"desativar_exc_{exc['id']}"):
                        session.execute(text("update excecoes_inconsistencia set ativa=false where id=:id"),
                                         {"id": exc["id"]})
                        session.commit()
                        st.rerun()
                else:
                    if st.button("Reativar", key=f"reativar_exc_{exc['id']}"):
                        session.execute(text("update excecoes_inconsistencia set ativa=true where id=:id"),
                                         {"id": exc["id"]})
                        session.commit()
                        st.rerun()
