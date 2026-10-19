-- W6 administrative roles. Grants are provisioned out of band by a DB administrator.
-- Never grant roles based solely on an OAuth email address or domain.
CREATE TABLE admin_user_roles (
    user_id uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    role text NOT NULL CHECK (role IN ('VIEWER', 'OPERATOR', 'ADMIN')),
    granted_by_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
    granted_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX admin_user_roles_role_idx ON admin_user_roles(role);

-- The existing audit table preserves human and service-credential provenance.
ALTER TABLE admin_audit_log
    ADD COLUMN actor_kind text NOT NULL DEFAULT 'LEGACY' CHECK (actor_kind IN ('LEGACY', 'API_KEY', 'USER_SESSION')),
    ADD COLUMN request_id text;
CREATE INDEX admin_audit_created_at_idx ON admin_audit_log(created_at DESC);
