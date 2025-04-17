import csv
import math

def read_nodes_from_csv(filepath):
    nodes = []
    with open(filepath, mode='r', newline='', encoding='utf-8') as csvfile:
        reader = csv.DictReader(csvfile)
        
        for row in reader:
            nodes.append({
                'id': row['ID'],
                'x': float(row['Latitude']),
                'y': float(row['Longitude'])
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
                lat1 = nodes[i]['x']
                lat2 = nodes[j]['x']
                long1 = nodes[i]['y'] 
                long2 = nodes[j]['y']
                distance[id_i][id_j] = haversine(lat1, long1, lat2, long2)
    return distance

def haversine(lat1, lon1, lat2, lon2):
     
    # distance between latitudes
    # and longitudes
    dLat = (lat2 - lat1) * math.pi / 180.0
    dLon = (lon2 - lon1) * math.pi / 180.0
 
    # convert to radians
    lat1 = (lat1) * math.pi / 180.0
    lat2 = (lat2) * math.pi / 180.0
 
    # apply formulae
    a = (pow(math.sin(dLat / 2), 2) +
         pow(math.sin(dLon / 2), 2) *
             math.cos(lat1) * math.cos(lat2))
    rad = 6371
    c = 2 * math.asin(math.sqrt(a))
    return rad * c


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

def get_distance_truck_to_deposit(latitude, longitude):
    
    return haversine(latitude, longitude, 41.1693 , -8.6026) #Update with the deposit coordinates

def get_latitude_from_deposit():
    return 41.1693 #Update with the deposit coordinates

def get_longitude_from_deposit():
    return -8.6026 #Update with the deposit coordinates

# testing
graph_nodes = read_nodes_from_csv("dataset/dataset.csv")
#print(graph_nodes)
#print(build_distance_dict(graph_nodes))
