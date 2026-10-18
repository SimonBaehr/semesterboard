-- Semesterboard: Tabellen, Zugriffsschutz (RLS) und Live-Updates.
-- Jede Zeile gehört genau einem Konto (owner). Ohne Anmeldung ist nichts lesbar.

create table if not exists public.courses (
  id text primary key,
  code text not null,
  name text not null,
  short text not null,
  lecturer text,
  exam text,
  ects int default 5,
  materials jsonb not null default '[]',
  owner uuid not null default auth.uid() references auth.users on delete cascade,
  created_at timestamptz not null default now()
);

create table if not exists public.events (
  id text primary key default gen_random_uuid()::text,
  title text not null,
  start_at timestamptz not null,
  end_at timestamptz,
  all_day boolean not null default false,
  location text default '',
  info text default '',
  course text default '',
  kind text not null default 'own' check (kind in ('lecture','deadline','exam','interim','other','holiday','private','own')),
  source text default 'dashboard',
  gcal text,                         -- null = noch nicht im Google Kalender, 'manual' = per Knopf, sonst Google-Event-ID
  owner uuid not null default auth.uid() references auth.users on delete cascade,
  created_at timestamptz not null default now()
);

create table if not exists public.tasks (
  id text primary key default gen_random_uuid()::text,
  title text not null,
  due date,
  course text default '',
  prio boolean not null default false,
  done boolean not null default false,
  done_at timestamptz,
  source text default 'dashboard',
  owner uuid not null default auth.uid() references auth.users on delete cascade,
  created_at timestamptz not null default now()
);

create table if not exists public.steps (
  id text primary key default gen_random_uuid()::text,
  course text not null,
  ord int not null default 0,
  title text not null,
  due date,
  owner_name text default '',
  done boolean not null default false,
  done_at timestamptz,
  owner uuid not null default auth.uid() references auth.users on delete cascade,
  created_at timestamptz not null default now()
);

create table if not exists public.notes (
  id text primary key default gen_random_uuid()::text,
  text text not null,
  course text default '',
  event_id text default '',
  owner uuid not null default auth.uid() references auth.users on delete cascade,
  created_at timestamptz not null default now()
);

create table if not exists public.digests (
  id text primary key,               -- Datum YYYY-MM-DD
  date date not null,
  summary text not null default '',
  items jsonb not null default '[]',
  tasks jsonb not null default '[]',
  owner uuid not null default auth.uid() references auth.users on delete cascade,
  created_at timestamptz not null default now()
);

create table if not exists public.literature (
  id text primary key default gen_random_uuid()::text,
  course text not null,
  ord int not null default 0,
  title text not null,
  authors text default '',
  year int,
  status int not null default 0 check (status between 0 and 2),
  owner uuid not null default auth.uid() references auth.users on delete cascade,
  created_at timestamptz not null default now()
);

create table if not exists public.timelog (
  id text primary key default gen_random_uuid()::text,
  course text not null,
  minutes int not null check (minutes > 0),
  date date not null default current_date,
  owner uuid not null default auth.uid() references auth.users on delete cascade,
  created_at timestamptz not null default now()
);

create table if not exists public.reviews (
  id text primary key,               -- ISO-Woche YYYY-Www
  week text not null,
  text text not null,
  owner uuid not null default auth.uid() references auth.users on delete cascade,
  created_at timestamptz not null default now()
);

-- Zugriffsschutz: nur eigene Zeilen lesen und schreiben
do $$
declare t text;
begin
  foreach t in array array['courses','events','tasks','steps','notes','digests','literature','timelog','reviews'] loop
    execute format('alter table public.%I enable row level security', t);
    execute format('drop policy if exists own_rows on public.%I', t);
    execute format('create policy own_rows on public.%I for all to authenticated using (owner = (select auth.uid())) with check (owner = (select auth.uid()))', t);
    execute format('create index if not exists %I on public.%I (owner)', t || '_owner_idx', t);
  end loop;
end $$;

-- Live-Updates an alle offenen Geräte
do $$
declare t text;
begin
  foreach t in array array['courses','events','tasks','steps','notes','digests','literature','timelog','reviews'] loop
    if not exists (select 1 from pg_publication_tables where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = t) then
      execute format('alter publication supabase_realtime add table public.%I', t);
    end if;
  end loop;
end $$;
