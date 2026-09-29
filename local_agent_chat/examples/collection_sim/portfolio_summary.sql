SELECT route_code, count(*) AS clients
FROM collection_sim.v_client_route
GROUP BY route_code
ORDER BY route_code;
