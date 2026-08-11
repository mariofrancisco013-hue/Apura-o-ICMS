-- ICMS Substituição — divisão Interno (dentro do Ceará) x Interestadual (pedido do usuário em 11/08/2026:
-- "preciso dividir entre dentro do estado e fora do estado o relatorio da 1076, o relatorio da sefaz só
-- serve para comprar as de fora do estado do Ceara, as internas deve ser tratada com base na informação da
-- planilha em anexa").
--
-- Achado ao investigar a planilha manual "ICMS INTERNO" do usuário (seção "4. OPERAÇÕES INTERNAS") e o
-- material de apoio "TRIBUTAÇÃO 2024.xlsx" (tabela de carga líquida do Decreto ICMS Nº 29.560/CE):
-- confirmado por conferência aritmética exata (NF 15525/CAPY: base R$5.599,70 × 3% "adicional" do Simples
-- = R$167,991, batendo exato com o ajuste manual da planilha; e a alíquota efetiva de 7,08% já usada pela
-- Rotina 1076 pra essa NF é exatamente a linha "20% Demais mercadorias, SIMPLES NACIONAL, própria estado"
-- do Decreto) que a Rotina 1076 JÁ aplica a alíquota correta na entrada, incluindo o adicional de Simples
-- Nacional quando é o caso — não existe um valor faltando pra calcular por fora. Por isso, confirmado com
-- o usuário ("traga com a aliquota que consta na 1076"): a aba Interno não recalcula nada, só agrupa por
-- NF os itens da Rotina 1076 com uf = 'CE' (mesma lógica de agrupamento da planilha manual do usuário).
--
-- Este cadastro (Plan1 da planilha "ICMS INTERNO" do usuário: CNPJ, Razão Social, Optante do Simples)
-- entra só como informação de apoio/auditoria na tela (não é usado em nenhum cálculo) — é global (não por
-- competência), igual o cadastro de CFOP.

create table if not exists cadastro_fornecedores_st (
    cnpj            text primary key,
    razao_social    text,
    simples         text,  -- "Sim" / "Não" / "ST RETIDO" (como vem na planilha do usuário)
    atualizado_em   timestamptz not null default now()
);

alter table cadastro_fornecedores_st enable row level security;

drop policy if exists "authenticated_full_access" on cadastro_fornecedores_st;
create policy "authenticated_full_access" on cadastro_fornecedores_st
    for all to authenticated using (true) with check (true);
