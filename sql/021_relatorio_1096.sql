-- Aba Antecipado (Receita 1023) — correção do usuário em 12/08/2026: "utilize esse relatorio no lugar do
-- da 1076, porque o codigo 1023 é antecipado, então não vai ser apresentado no da 1076".
--
-- A migração anterior (020_rotina_1076_antecipado.sql) criou `rotina_1076_antecipado_itens` pra guardar um
-- layout "analítico" da Rotina 1076 pensado pra esta aba. Só que a Receita 1023 (Antecipado) não passa pela
-- Rotina 1076 de jeito nenhum — por isso essa tabela nunca vai ter as NFs que a aba Antecipado precisa
-- mostrar. O relatório certo pra isso é o "Relatório 1096" do Winthor (confirmado pelo usuário com um
-- arquivo real, "1096 relatório 13.xlsx"). Esta migração cria a tabela nova; a `rotina_1076_antecipado_itens`
-- fica sem uso (não é apagada, só não é mais referenciada pelo app — sem problema deixar parada).

create table if not exists relatorio_1096_itens (
    id                  bigserial primary key,
    competencia_id      bigint not null references competencias(id) on delete cascade,
    nf_numero           text not null,
    dt_emissao          date,
    produto_codigo      text,
    produto_descricao   text,
    cfop                text,
    quantidade          numeric(14,3),
    valor_produto       numeric(14,2),
    cst                 text,
    base_icms           numeric(14,2),
    aliq_icms           numeric(9,4),
    valor_icms          numeric(14,2),
    aliq_pis            numeric(9,4),
    valor_pis           numeric(14,2),
    aliq_cofins         numeric(9,4),
    valor_cofins        numeric(14,2),
    importado_em        timestamptz not null default now()
);
create index if not exists ix_relatorio_1096_itens_competencia
    on relatorio_1096_itens(competencia_id);
create index if not exists ix_relatorio_1096_itens_nf
    on relatorio_1096_itens(competencia_id, nf_numero);

alter table relatorio_1096_itens enable row level security;

drop policy if exists "authenticated_full_access" on relatorio_1096_itens;
create policy "authenticated_full_access" on relatorio_1096_itens
    for all to authenticated using (true) with check (true);
