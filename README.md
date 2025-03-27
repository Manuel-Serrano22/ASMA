# Projeto - Agentes e Sistemas Multi-Agente 

## Estatísticas
Variáveis necessárias para implementar as métricas pedidas no projeto

**Truck**

 - Variável que vá guardando a quantidade de lixo recolhido;
 - Variável que guarda a distância percorrida;

**Bin**

 - Variável que indica o limite de enchimento;
 - Variável que indica a taxa de enchimento do lixo;
 - Variável (array) para guardar o nível de lixo recolhido em cada recolha;
 - Behaviour que contabiliza o tempo em que o Bin esteve acima do limite e a quantidade excedente acumulada;

## Requisitos

**Truck**

 - Começam no depósito e, em seguida, deslocam-se para o centro do grafo;
 - Ficam parados até receberem alertas dos Bins;
 - Quando recebem uma proposta de um Bin, respondem com a quantidade de espaço disponível e a distância até ao Bin;
 - Cada Truck só pode ter um contrato ativo de cada vez;
 - Quando estiverem contratados, se passarem por outros Bins, podem perguntar pelas capacidades e recolher lixo se ainda tiverem espaço disponível;
 - Quando atingirem x% da sua capacidade máxima, dirigem-se ao local de depósito para descarregar o lixo;

**Bin**

 - Quando atinge o limite de enchimento, envia propostas com a sua posição no grafo;
 - Após receber todas as respostas, escolhe o camião mais próximo que tenha espaço disponível para recolher o lixo;
 - Periodicamente, envia atualizações sobre o seu nível de enchimento aos Trucks;
- **Dúvida**: o que acontece caso não existam Trucks disponiveis??

## Behaviours

**Truck**

 - FSM, recebe prospota + calcula distância ao Bin + envia resposta + espera resposta + executa tarefa (caso seja escolhido);
 - Periodicamente, verificar a sua capacidade => Periódico;

**Bin**

- Taxa de enchimento => Periódico;
- Quando o nível de lixo atinge o limite, o comportamento anterior começa a contar o tempo e a quantidade de lixo que ultrapassa o limite;
- Periodicamente, envia a sua capacidade atual aos Trucks; => Periódico;
- FSM para gerir os acordos com os Trucks (propostas e seleção);
- O primeiro estado da FSM pode ser a taxa de enchimento;