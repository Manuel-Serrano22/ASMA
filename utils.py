import csv
import math

def read_nodes_from_csv(filepath):
    nodes = []
    with open(filepath, mode='r', newline='', encoding='utf-8') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            nodes.append({
                'id': row['Agent ID'],
                'x': float(row['Position_X']),
                'y': float(row['Position_Y'])
            })
    return nodes

def build_distance_dict(nodes):
    distance = {}

    for i in range(len(nodes)):
        id_i = nodes[i]['id']
        distance[id_i] = {}
        for j in range(len(nodes)):
            id_j = nodes[j]['id']
            if id_i == id_j:
                distance[id_i][id_j] = 0.0
            else:
                dx = nodes[i]['x'] - nodes[j]['x']
                dy = nodes[i]['y'] - nodes[j]['y']
                distance[id_i][id_j] = math.hypot(dx, dy)
    return distance

def get_distance_between_agents(agent1_id, agent2_id, distance_dict):
    try:
        return distance_dict[agent1_id][agent2_id]
    except KeyError:
        raise ValueError("One or both agent IDs not found in the distance dictionary.")