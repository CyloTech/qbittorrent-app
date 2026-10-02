-- Publish the verified candidate and retain the previous version for rollback.
BEGIN;
DO $publish$
DECLARE
    affected integer;
BEGIN
    PERFORM 1 FROM apps WHERE id=211 AND version='5.2.3_2.0.13.0'
        AND tag='5.2.3_2.0.13.0' AND "Image"='qbittorrent'
        AND registry='repo.cylo.io' FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'App default changed'; END IF;
    PERFORM 1 FROM app_versions WHERE app_id=211 AND version='5.2.4_2.0.15.0'
        AND tag='5.2.4_2.0.15.0' AND is_default=0 AND enabled=1 AND admin_only=1
        AND installed_image_digest='repo.cylo.net/qbittorrent@sha256:bfb8197efda742ec58d88543c75885c18aadc32143442564d3595bcf9a6a7ffd' AND length(changes)>0 FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'Release candidate changed'; END IF;
    UPDATE app_versions SET is_default=0,updated_at=now()
        WHERE id=1299 AND app_id=211 AND version='5.2.3_2.0.13.0'
            AND enabled=1 AND is_default=1;
    GET DIAGNOSTICS affected=ROW_COUNT;
    IF affected<>1 THEN RAISE EXCEPTION 'Expected one previous default'; END IF;
    UPDATE app_versions SET is_default=1,admin_only=0,updated_at=now()
        WHERE app_id=211 AND version='5.2.4_2.0.15.0'
            AND is_default=0 AND enabled=1 AND admin_only=1
            AND installed_image_digest='repo.cylo.net/qbittorrent@sha256:bfb8197efda742ec58d88543c75885c18aadc32143442564d3595bcf9a6a7ffd';
    GET DIAGNOSTICS affected=ROW_COUNT;
    IF affected<>1 THEN RAISE EXCEPTION 'Expected one new default'; END IF;
    UPDATE apps SET version='5.2.4_2.0.15.0',tag='5.2.4_2.0.15.0',
        registry='repo.cylo.net',updated_at=now()
        WHERE id=211 AND version='5.2.3_2.0.13.0' AND tag='5.2.3_2.0.13.0'
            AND "Image"='qbittorrent' AND registry='repo.cylo.io';
    GET DIAGNOSTICS affected=ROW_COUNT;
    IF affected<>1 THEN RAISE EXCEPTION 'Expected one app default update'; END IF;
    IF (SELECT count(*) FROM app_versions WHERE app_id=211 AND is_default=1)<>1
    THEN RAISE EXCEPTION 'Default version count mismatch'; END IF;
END;
$publish$;
COMMIT;
