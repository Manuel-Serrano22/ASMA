import spade
from spade.agent import Agent
import asyncio
import os
from dotenv import load_dotenv
import truck as truck
import bin as bin
import pandas as pd
from utils import *
import world as world
load_dotenv()
SPADE_PASS = os.getenv('SPADE_PASS')

async def main():
    # read information from dataset
    #df = pd.read_csv("dataset/dataset.csv") -> dataset completo
    df = pd.read_csv("dataset/1bin.csv")

    # base port
    port = 10000

    # Provided by the user via terminal -> Time interval(bin filling rate)
    # Bin filling rate (quantity), Number of trucks
    num_trucks = int(input("Enter the number of trucks: "))
    bin_filling_rate_time = int(input("Enter bin filling rate time interval: "))
    bin_filling_rate_quantity = int(input("Enter bin filling rate quantity: "))

    # trash per second
    bin_filling = bin_filling_rate_quantity / bin_filling_rate_time
    print("Bin filling rate =" + str(bin_filling) + "trash/second")
    # contact list for bin -> it receives the address
    contact_list = []
    deposito_row = df.iloc[-1]
    deposito_lat = deposito_row['Latitude']
    deposito_long = deposito_row['Longitude']
    for i in range(num_trucks):
        truck_jid = "agente" + str(i) + "@localhost"
        # update the contact_list
        contact_list.append(truck_jid)
        print("Creating truck agent with JID: " + truck_jid)
        # jid, password, latitude, longitude, capacity
        truck_agent = truck.TruckAgent(truck_jid, SPADE_PASS, deposito_lat, deposito_long)
        await truck_agent.start()
        port_str = str(port)
        truck_agent.web.start(hostname="127.0.0.1", port=port_str)
        await asyncio.sleep(3)
        port += 1

    for _, row in df[:-1].iterrows():
        jid = "agente" + row['ID'] + "@localhost"
        print("Creating bin agent with JID: " + jid)
        iD = row['ID']
        latitude = row['Latitude']
        longitude = row['Longitude']
        fsmagent = bin.BinAgent(jid, SPADE_PASS, iD, contact_list, latitude, longitude)
        await fsmagent.start(auto_register=True)
        port_str = str(port)
        fsmagent.web.start(hostname="127.0.0.1", port=port_str)
        await asyncio.sleep(3)
        port += 1
    
    # Register and start world agent to collect statistics
    fsmagent = world.WorldAgent("world@localhost", SPADE_PASS)
    await fsmagent.start(auto_register=True)
    port_str = str(port)
    fsmagent.web.start(hostname="127.0.0.1", port=port_str)

    sim_duration = 30 # seconds
    await asyncio.sleep(sim_duration)

    # # If the simulation ends while the bin is still full, the time_full_start is still running 
    if fsmagent.time_full_start is not None:
        duration = fsmagent.time - fsmagent.time_full_start
        fsmagent.total_time_full += duration
        fsmagent.time_full_start = None

    print("\n--- BIN METRICS ---")
    print(f"Total time bin was full: {fsmagent.total_time_full:.2f} seconds")
    print(f"Total overflow accumulated: {fsmagent.total_overflow:.2f} units")

    await spade.wait_until_finished(truck_agent)
    await spade.wait_until_finished(fsmagent)
    await fsmagent.stop()
    print("BIN: Agent finished")

    await truck_agent.stop()
    print("TRUCK: Agent finished")

if __name__ == "__main__":
    spade.run(main())