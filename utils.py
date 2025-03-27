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


def print_distance_matrix(distance_dict):
    ids = list(distance_dict.keys())
    print(f"{'':10}", end="")
    for id_j in ids:
        print(f"{id_j:10}", end="")
    print()
    for id_i in ids:
        print(f"{id_i:10}", end="")
        for id_j in ids:
            print(f"{distance_dict[id_i][id_j]:10.2f}", end="")
        print()


def get_distance_between_agents(agent1_id, agent2_id, distance_dict):
    try:
        return distance_dict[agent1_id][agent2_id]
    except KeyError:
        raise ValueError("One or both agent IDs not found in the distance dictionary.")


def get_node_closest_to_center(nodes):
    avg_x = sum(node['x'] for node in nodes) / len(nodes)
    avg_y = sum(node['y'] for node in nodes) / len(nodes)

    street_nodes = [node for node in nodes if node['id'].startswith('street')]

    min_distance = float('inf')
    closest_node = None

    for node in street_nodes:
        dx = node['x'] - avg_x
        dy = node['y'] - avg_y
        dist = math.hypot(dx, dy)

        if dist < min_distance:
            min_distance = dist
            closest_node = node

    return closest_node


def get_distance_truck_to_bin(truck_position, bin_position):

    dx = truck_position[0] - bin_position[0]
    dy = truck_position[1] - bin_position[1]
    
    return math.hypot(dx, dy)