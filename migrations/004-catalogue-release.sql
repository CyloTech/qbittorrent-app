-- Register 5.2.4_2.0.15.0-4 as a private release candidate by copying the
-- -2 resource contract and its version-specific fields. -3 was pushed to the
-- registry but never registered; -4 contains it plus the background callback.
-- Run only after the exact image passes the release gate and registry verification.
BEGIN;
DO $release$
DECLARE
    baseline app_versions%ROWTYPE;
    candidate app_versions%ROWTYPE;
    new_id integer;
    affected integer;
BEGIN
    PERFORM 1 FROM apps WHERE id=211 AND version='5.2.4_2.0.15.0-2'
        AND tag='5.2.4_2.0.15.0-2' AND app_slots=1 AND "Memory"=100
        AND "MemorySwap"=100 AND "MemoryReservation"=4 AND "CPUs"=0
        AND allow_downgrade=1 AND registry='repo.cylo.net' AND "Image"='qbittorrent' FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'App baseline changed'; END IF;
    SELECT * INTO STRICT baseline FROM app_versions
        WHERE id=1418 AND app_id=211 AND version='5.2.4_2.0.15.0-2'
        AND is_default=1 AND enabled=1 AND admin_only=0
        AND tag='5.2.4_2.0.15.0-2'
        AND installed_image_digest='repo.cylo.net/qbittorrent@sha256:7df0fd9b6ada07bb5cb82cfd00b5dee489eaf323c4014eb32cc1e6c5532c0a6a' FOR UPDATE;
    IF baseline.memory<>100 OR baseline.memory_swap<>100
        OR baseline.memory_reservation<>4 OR baseline.cpus<>0
        OR baseline.app_slots<>1 OR baseline.combined_dynamic_ports<>2
    THEN RAISE EXCEPTION 'Version resource baseline changed'; END IF;
    IF EXISTS (SELECT 1 FROM app_versions WHERE app_id=211
        AND version IN ('5.2.4_2.0.15.0-3','5.2.4_2.0.15.0-4'))
    THEN RAISE EXCEPTION 'Release already registered'; END IF;

    INSERT INTO app_versions (
        app_id,version,tag,enabled,is_default,changes,min_memory,min_cpus,
        deprecated,deprecation_message,created_at,updated_at,user_id,admin_only,
        memory,memory_swap,memory_reservation,cpus,init,privileged,cap_add,cap_drop,
        tcp_port_range,udp_port_range,tcp_dynamic_ports,udp_dynamic_ports,pids_limit,
        combined_port_range,combined_dynamic_ports,app_slots,image,
        custom_field_preinstall_description,custom_field_postinstall_description,installed_image_digest
    ) SELECT app_id,'5.2.4_2.0.15.0-4','5.2.4_2.0.15.0-4',1,0,
        'Fixes qBittorrent getting stuck on "Updating" when the Appbox was restarted during an install or update. qBittorrent also starts without waiting for Appbox to confirm the install. Torrent data is now read and written with POSIX disk I/O by default instead of memory-mapped files, which avoids heavy memory use on busy servers. If you already chose a disk I/O type in qBittorrent''s advanced settings, your choice is kept. Your torrents, settings and the API address used by Sonarr, Radarr and similar apps stay the same when you update.',
        min_memory,min_cpus,0,NULL,now(),now(),2,1,
        memory,memory_swap,memory_reservation,cpus,init,privileged,cap_add,cap_drop,
        tcp_port_range,udp_port_range,tcp_dynamic_ports,udp_dynamic_ports,pids_limit,
        combined_port_range,combined_dynamic_ports,app_slots,image,
        custom_field_preinstall_description,custom_field_postinstall_description,'repo.cylo.net/qbittorrent@sha256:b41d7757d9c2c401367e2343b46c50dbf4eb5d562d1d3e912e792a2b59e825f4'
        FROM app_versions WHERE id=baseline.id RETURNING id INTO new_id;
    GET DIAGNOSTICS affected=ROW_COUNT;
    IF affected<>1 THEN RAISE EXCEPTION 'Expected one new version'; END IF;
    SELECT * INTO STRICT candidate FROM app_versions WHERE id=new_id;
    IF (to_jsonb(candidate)-ARRAY['id','version','tag','enabled','is_default','changes','deprecated','deprecation_message','created_at','updated_at','user_id','admin_only','installed_image_digest'])
        IS DISTINCT FROM
       (to_jsonb(baseline)-ARRAY['id','version','tag','enabled','is_default','changes','deprecated','deprecation_message','created_at','updated_at','user_id','admin_only','installed_image_digest'])
    THEN RAISE EXCEPTION 'Resource parity failed'; END IF;

    INSERT INTO customfields (
        type,relid,relid2,fname,display_name,fieldtype,description,default_value,
        required,options,regex,adminonly,sort_order,flex,customtable_editable,
        template_type,"minLength","maxLength",version,app_version_id,stable_id,
        condition_json,sensitive,revealable,"unique"
    ) SELECT type,relid,relid2,fname,display_name,fieldtype,description,default_value,
        required,options,regex,adminonly,sort_order,flex,customtable_editable,
        template_type,"minLength","maxLength",'5.2.4_2.0.15.0-4',new_id,stable_id,
        condition_json,sensitive,revealable,"unique"
        FROM customfields WHERE id IN (1446,1447) AND type='app' AND relid=211
            AND version=baseline.version AND app_version_id=baseline.id
            AND ((fname='PASSWORD' AND fieldtype='complexPassword')
                OR (fname='ENABLE_VUETORRENT' AND fieldtype='selector'));
    GET DIAGNOSTICS affected=ROW_COUNT;
    IF affected<>2 THEN RAISE EXCEPTION 'Expected two version-specific fields'; END IF;
END;
$release$;
COMMIT;
