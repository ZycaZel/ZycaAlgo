-- ZycaAlgo account infrastructure (Phase 1).
-- Run this once in the Supabase SQL Editor (Project -> SQL Editor -> New query).
--
-- Supabase already provides `auth.users` (email, password, session handling)
-- out of the box - this just adds the one column ZycaAlgo actually needs on
-- top of that: which mode a trader has chosen.

create table if not exists public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  email text not null,
  mode text not null default 'manual' check (mode in ('manual', 'ai_managed')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- Row Level Security: a user can only ever read/write their own row. This is
-- what makes it safe to embed the Supabase "anon" key directly in the site's
-- client-side JS (its own documented usage pattern) - the anon key alone
-- can't read or modify any other trader's data.
alter table public.profiles enable row level security;

create policy "select own profile"
  on public.profiles for select
  using (auth.uid() = id);

create policy "update own profile"
  on public.profiles for update
  using (auth.uid() = id);

-- New signups get a profiles row automatically (default mode: 'manual'),
-- rather than relying on client-side code to remember to create one.
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer set search_path = public
as $$
begin
  insert into public.profiles (id, email)
  values (new.id, new.email);
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- Keep updated_at current whenever a profile row changes (e.g. mode switch).
create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists on_profile_updated on public.profiles;
create trigger on_profile_updated
  before update on public.profiles
  for each row execute function public.set_updated_at();
