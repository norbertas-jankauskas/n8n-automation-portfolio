create table public.invoices (
  id text primary key,
  client text not null,
  status text not null,
  total_cents bigint not null check (total_cents >= 0 and total_cents < 10000000),
  issued_at date not null,
  iso_week text not null
);
create table public.kpi_weekly (
  iso_week text primary key,
  revenue_paid_cents bigint not null,
  invoices_paid int not null,
  invoices_open int not null,
  avg_paid_cents bigint not null
);
alter table public.invoices enable row level security;
alter table public.kpi_weekly enable row level security;
grant usage on schema public to anon;
grant select, insert, update on public.invoices to anon;
grant select, insert, update on public.kpi_weekly to anon;
create policy apc_anon_select on public.invoices for select to anon using (true);
create policy apc_anon_insert on public.invoices for insert to anon with check (true);
create policy apc_anon_update on public.invoices for update to anon using (true) with check (true);
create policy apc_anon_select on public.kpi_weekly for select to anon using (true);
create policy apc_anon_insert on public.kpi_weekly for insert to anon with check (true);
create policy apc_anon_update on public.kpi_weekly for update to anon using (true) with check (true);
