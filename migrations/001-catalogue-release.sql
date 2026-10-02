-- Register a private release candidate by copying the current resource contract.
-- Run only after the exact image passes the release gate and registry verification.
BEGIN;
DO $release$
DECLARE
    baseline app_versions%ROWTYPE;
    candidate app_versions%ROWTYPE;
    new_id integer;
    affected integer;
BEGIN
    PERFORM 1 FROM apps WHERE id=211 AND version='5.2.3_2.0.13.0'
        AND tag='5.2.3_2.0.13.0' AND app_slots=1 AND "Memory"=100
        AND "MemorySwap"=100 AND "MemoryReservation"=4 AND "CPUs"=0
        AND allow_downgrade=1 FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'App baseline changed'; END IF;
    SELECT * INTO STRICT baseline FROM app_versions
        WHERE id=1299 AND app_id=211 AND version='5.2.3_2.0.13.0'
        AND is_default=1 AND enabled=1 FOR UPDATE;
    IF baseline.memory<>100 OR baseline.memory_swap<>100
        OR baseline.memory_reservation<>4 OR baseline.cpus<>0
        OR baseline.app_slots<>1 OR baseline.combined_dynamic_ports<>2
    THEN RAISE EXCEPTION 'Version resource baseline changed'; END IF;
    IF EXISTS (SELECT 1 FROM app_versions WHERE app_id=211 AND version='5.2.4_2.0.15.0')
    THEN RAISE EXCEPTION 'Release already registered'; END IF;
    INSERT INTO app_versions (
        app_id,version,tag,enabled,is_default,changes,min_memory,min_cpus,
        deprecated,deprecation_message,created_at,updated_at,user_id,admin_only,
        memory,memory_swap,memory_reservation,cpus,init,privileged,cap_add,cap_drop,
        tcp_port_range,udp_port_range,tcp_dynamic_ports,udp_dynamic_ports,pids_limit,
        combined_port_range,combined_dynamic_ports,app_slots,image,
        custom_field_preinstall_description,custom_field_postinstall_description,installed_image_digest
    ) SELECT app_id,'5.2.4_2.0.15.0','5.2.4_2.0.15.0',1,0,
        'Updated qBittorrent to 5.2.4 with libtorrent 2.0.15. Includes fixes for duplicate torrent additions and file renaming, plus a WebUI dialog for adding multiple torrents. Select this version in Appbox to update your installation.',
        min_memory,min_cpus,0,NULL,now(),now(),2,1,
        memory,memory_swap,memory_reservation,cpus,init,privileged,cap_add,cap_drop,
        tcp_port_range,udp_port_range,tcp_dynamic_ports,udp_dynamic_ports,pids_limit,
        combined_port_range,combined_dynamic_ports,app_slots,image,
        custom_field_preinstall_description,custom_field_postinstall_description,'repo.cylo.net/qbittorrent@sha256:bfb8197efda742ec58d88543c75885c18aadc32143442564d3595bcf9a6a7ffd'
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
        template_type,"minLength","maxLength",'5.2.4_2.0.15.0',new_id,stable_id,
        condition_json,sensitive,revealable,"unique"
        FROM customfields WHERE id=1119 AND type='app' AND relid=211
            AND version=baseline.version AND app_version_id=baseline.id
            AND fname='PASSWORD' AND fieldtype='complexPassword';
    GET DIAGNOSTICS affected=ROW_COUNT;
    IF affected<>1 THEN RAISE EXCEPTION 'Expected one version-specific field'; END IF;
END;
$release$;
COMMIT;
