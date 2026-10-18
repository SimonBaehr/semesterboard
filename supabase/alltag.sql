-- Alltag: Training, Ernährung, Gewicht. Eigene Tabellen (Präfix fit_), getrennt von den Uni-Tabellen.
-- Gleicher Zugriffsschutz wie dort: jede Zeile gehört genau einem Konto (owner).

create table if not exists public.fit_workouts (
  id text primary key default gen_random_uuid()::text,
  date date not null default current_date,
  time text default '',              -- HH:MM, leer = ohne Uhrzeit
  type text not null default 'kraft' check (type in ('kraft','ausdauer','sport','mobility','sonstiges')),
  title text not null,
  duration_min int check (duration_min is null or duration_min > 0),
  kcal int check (kcal is null or kcal >= 0),  -- verbrannte Kalorien
  details text default '',           -- Übungen, Sätze, Strecke …
  done boolean not null default false,
  done_at timestamptz,
  owner uuid not null default auth.uid() references auth.users on delete cascade,
  created_at timestamptz not null default now()
);

create table if not exists public.fit_meals (
  id text primary key default gen_random_uuid()::text,
  date date not null default current_date,
  meal text not null default 'snack' check (meal in ('fruehstueck','mittag','abend','snack')),
  title text not null,
  kcal int not null default 0 check (kcal >= 0),
  protein numeric(5,1) check (protein is null or protein >= 0),
  owner uuid not null default auth.uid() references auth.users on delete cascade,
  created_at timestamptz not null default now()
);

create table if not exists public.fit_weight (
  id text primary key default gen_random_uuid()::text,
  date date not null default current_date,
  kg numeric(5,1) not null check (kg between 20 and 400),
  owner uuid not null default auth.uid() references auth.users on delete cascade,
  created_at timestamptz not null default now(),
  unique (owner, date)
);

create table if not exists public.fit_goals (
  id text primary key,               -- = Konto-ID, eine Zeile pro Konto
  kcal_target int,                   -- Ziel: gegessene kcal pro Tag
  base_burn int,                     -- Grundverbrauch pro Tag ohne Training
  protein_target int,
  weight_target numeric(5,1),
  owner uuid not null default auth.uid() references auth.users on delete cascade,
  created_at timestamptz not null default now()
);

do $$
declare t text;
begin
  foreach t in array array['fit_workouts','fit_meals','fit_weight','fit_goals'] loop
    execute format('alter table public.%I enable row level security', t);
    execute format('drop policy if exists own_rows on public.%I', t);
    execute format('create policy own_rows on public.%I for all to authenticated using (owner = (select auth.uid())) with check (owner = (select auth.uid()))', t);
    execute format('create index if not exists %I on public.%I (owner)', t || '_owner_idx', t);
    if not exists (select 1 from pg_publication_tables where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = t) then
      execute format('alter publication supabase_realtime add table public.%I', t);
    end if;
  end loop;
end $$;
