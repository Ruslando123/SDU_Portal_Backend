-- Local dedicated-cluster roles; schema ownership never belongs to the runtime.
DO $roles$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'portal_owner') THEN
        CREATE ROLE portal_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'portal_runtime') THEN
        CREATE ROLE portal_runtime NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'portal_cleanup') THEN
        CREATE ROLE portal_cleanup NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'portal_fixture') THEN
        CREATE ROLE portal_fixture NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'portal_observer') THEN
        CREATE ROLE portal_observer NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
    END IF;
    EXECUTE pg_catalog.format('GRANT portal_owner TO %I', current_user);
    EXECUTE pg_catalog.format('REVOKE ALL ON DATABASE %I FROM PUBLIC', current_database());
    EXECUTE pg_catalog.format('GRANT CONNECT ON DATABASE %I TO portal_runtime, portal_cleanup, portal_fixture, portal_observer', current_database());
END
$roles$;

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
CREATE SCHEMA audit AUTHORIZATION portal_owner;
REVOKE ALL ON SCHEMA audit FROM PUBLIC;
CREATE SCHEMA catalog AUTHORIZATION portal_owner;
REVOKE ALL ON SCHEMA catalog FROM PUBLIC;
CREATE SCHEMA enrollment AUTHORIZATION portal_owner;
REVOKE ALL ON SCHEMA enrollment FROM PUBLIC;
CREATE SCHEMA identity AUTHORIZATION portal_owner;
REVOKE ALL ON SCHEMA identity FROM PUBLIC;
CREATE SCHEMA operations AUTHORIZATION portal_owner;
REVOKE ALL ON SCHEMA operations FROM PUBLIC;
CREATE SCHEMA rules AUTHORIZATION portal_owner;
REVOKE ALL ON SCHEMA rules FROM PUBLIC;
CREATE SCHEMA sections AUTHORIZATION portal_owner;
REVOKE ALL ON SCHEMA sections FROM PUBLIC;
CREATE SCHEMA students AUTHORIZATION portal_owner;
REVOKE ALL ON SCHEMA students FROM PUBLIC;
