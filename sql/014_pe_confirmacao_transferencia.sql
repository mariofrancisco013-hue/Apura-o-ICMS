-- Confirmação manual de CFOPs de transferência na Apuração ICMS PE (Crédito Presumido) — pedido do usuário
-- em 10/08/2026: "quando aparecer os CFOP de transferência seja na entrada ou saida seja alertado para
-- conferência, e um botão de verdadeiro sim ou não, se não, solicitar a reimportação da 1024 corrigida".
--
-- CFOP de transferência (is_transferencia=true na tabela cfop) costuma exigir atenção redobrada na
-- Apuração PE porque, ao contrário do ICMS Normal (que já trata is_st+is_transferencia como sem efeito
-- automaticamente), o modelo de Crédito Presumido soma esses CFOPs junto com os demais nas linhas normais
-- (1.1/2.1/2.3) — então um CFOP de transferência importado errado na Rotina 1024 (ex: por engano, ou uma
-- filial nova que ainda não está cadastrada como do grupo) pode distorcer a apuração sem nenhum aviso.
--
-- Tabela genérica (não só para transferência: `tipo` permite reaproveitar para outras confirmações no
-- futuro, mesmo padrão de extensibilidade já usado em checkpoints_referencia.fonte).
create table if not exists confirmacoes_apuracao (
    id                    bigserial primary key,
    competencia_id        bigint not null references competencias(id) on delete cascade,
    tipo                  text not null,  -- ex: 'cfop_transferencia_pe'
    confirmado            boolean not null,
    observacao            text,
    confirmado_por        uuid references auth.users(id),
    confirmado_por_email  text,
    confirmado_em         timestamptz not null default now(),
    unique (competencia_id, tipo)
);
comment on table confirmacoes_apuracao is
    'Confirmações manuais do analista sobre pontos de atenção da apuração (ex: CFOPs de transferência '
    'encontrados na Rotina 1024 da Apuração PE) — confirmado=true libera o cálculo, confirmado=false trava '
    'e pede reimportação da fonte corrigida. Reimportar a Rotina 1024 (ver salvar_checkpoint_1024_pe) apaga '
    'a confirmação anterior, obrigando uma nova conferência a cada reimportação.';

alter table confirmacoes_apuracao enable row level security;
drop policy if exists "authenticated_full_access" on confirmacoes_apuracao;
create policy "authenticated_full_access" on confirmacoes_apuracao
    for all to authenticated using (true) with check (true);
