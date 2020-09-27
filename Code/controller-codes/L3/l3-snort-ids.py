#collect flow features "duration, packet-count, byte-count"
#run on terminal "ryu-manager l3-snort-ids.py"

#drop rules on code success
#snort alert -->> controller (get source mac) --> drop rule configure on all datapaths
#28-1-2020, error no flow added to the flow-table when "eth_type=ether_types.ETH_TYPE_IP" is not added to OFPMatch()  
#30-1-2020, success, error fix by "match = parser.OFPMatch(eth_type=ether_types.ETH_TYPE_IP, in_port=in_port, eth_src=src, ipv4_src=srcip, ipv4_dst=dstip)"
#30-1-2020, success, idle_timeout= 10 seconds set for packet-in-handler 
#30-1-2020, fail,flow statstics collection 
#30-1-2020, flow statstics collection, self.monitor_thread = hub.spawn(self.monitor) at every 10 seconds, idle-timeout = 10 seconds 

from __future__ import print_function
import array
import timeit
import os
import pandas as pd
import sklearn.preprocessing import minmax_scale 
import sklearn.neural_network import MLPClassifier
#import psutil
from operator import attrgetter
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, DEAD_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet
from ryu.lib.packet import ethernet
from ryu.lib.packet import ether_types
from ryu.lib.packet import ipv4
from ryu.lib.packet import icmp
from ryu.lib import snortlib
from ryu.lib import hub
count1 = 0
count2 = 0
pid=psutil.Process(os.getpid())

class L3snort(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
    _CONTEXTS = {'snortlib': snortlib.SnortLib}
   
    def __init__(self, *args, **kwargs):
        super(L3snort, self).__init__(*args, **kwargs)
        self.snort = kwargs['snortlib']
        self.snort_port = 3
        self.mac_to_port = {}
	self.datapaths = {}
        #UNIX DOMAIN SOCKET
        socket_config = {'unixsock': True}  
        #NETWORK SOCKET CONF    
        #socket_config = {'unixsock': False}    

        self.snort.set_config(socket_config)
        self.snort.start_socket_server()

	file = open("snort_alert.txt", "a+")
	file.write('\n Alert_msg,Source,Destination')
	self.IDS_training()
	self.monitor_thread = hub.spawn(self.monitor)
        file = open("Predict.txt", "a+")
        file.write('\n dp_id,duration,in_port,eth_src,ipv4_dst,packets,bytes')
        file.close()

    def monitor(self):
	
	self.logger.info("start flow monitoring")
        while True:    	    
            for dp in self.datapaths.values():
		self.logger.debug('send stats request: %016x', dp.id)
        	ofproto = dp.ofproto
        	parser = dp.ofproto_parser
        	# To collect dp_id, duration, pkt_count, byte_count
		req = parser.OFPFlowStatsRequest(dp)
        	dp.send_msg(req)	           
            hub.sleep(10)

    # Model training
    def IDS_training(self):
	# Read cleaned flow statistics data
	X_train = pd.read_csv('/home/ryu-snort/same-net.csv')
	y_train = X_train["class"]
	del X_train["class"]
	X_train.iloc[:]= minmax_scale(X_train.iloc[:])
	mlp = MLPClassifier(hidden_layer_size(7), activation="logistic", solver='sgd',beta_1=0.5, beta_2=0.5, 
		            learning_rate="constant", learning_rate_init=1, momentum=1)
	mlp.fit(X_train, y_train.values.ravel())

    def packet_print(self, msg):
        pkt = packet.Packet(array.array('B', msg.pkt))        
        eth = pkt.get_protocol(ethernet.ethernet)
        _ipv4 = pkt.get_protocol(ipv4.ipv4)
        _icmp = pkt.get_protocol(icmp.icmp)
        #src_ip = _ipv4.src 
        src_eth = eth.src

        if _icmp:
            self.logger.info("%r", _icmp)
        if _ipv4:
            self.logger.info("%r", _ipv4)
        if eth:
            self.logger.info("%r", eth)

	print( "drop rules set in datapaths: block all  traffiic from  source : %s"%src_eth)
	for datapath in self.datapaths.values():
		#print( "drop rules set in datapaths")
		ofproto = datapath.ofproto
		parser = datapath.ofproto_parser
		match = parser.OFPMatch(eth_src = src_eth)
		action	= []		      
		self.add_flow(datapath, 2, match, action)
	file = open("snort_alert.txt", "a+")
	#print(msg.alertmsg[0])
	
	file.write("\n" + str(msg.alertmsg[0]) + "," + str(src_eth) + "," +  str(_ipv4.dst))

    @set_ev_cls(snortlib.EventAlert, MAIN_DISPATCHER)
    def _dump_alert(self, ev):
	start = timeit.default_timer()
        global count2
        count2 = count2 + 1		      
	msg = ev.msg	
	
        self.logger.info('alertmsg: %s' % ''.join(msg.alertmsg))
        self.packet_print(msg)
        t = timeit.default_timer()-start
	self.logger.info("Alert: %s take : %s  seconds",count2,t)

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser	
	self.datapaths[datapath.id] = datapath
        # install table-miss flow entry
        #
        # We specify NO BUFFER to max_len of the output action due to
        # OVS bug. At this moment, if we specify a lesser number, e.g.,
        # 128, OVS will send Packet-In with invalid buffer_id and
        # truncated packet data. In that case, we cannot output packets
        # correctly
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER,
                                          ofproto.OFPCML_NO_BUFFER)]
        self.add_flow(datapath, 0, match, actions)

    def add_flow(self, datapath, priority, match, actions, idle=0):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
	
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS,
                                             actions)]

        mod = parser.OFPFlowMod(datapath=datapath, priority=priority,
                                match=match, idle_timeout=idle, instructions=inst)
        datapath.send_msg(mod)
	
    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
    	global count1
	packetin = open("packet_in.txt","a+")        
	start = timeit.default_timer()
	msg = ev.msg
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        in_port = msg.match['in_port']

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocols(ethernet.ethernet)[0]
        dst = eth.dst
        src = eth.src

        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            # ignore lldp packet
            return
          
        dpid = datapath.id
        self.mac_to_port.setdefault(dpid, {})        

        # learn a mac address to avoid FLOOD next time.
        self.mac_to_port[dpid][src] = in_port

        if dst in self.mac_to_port[dpid]:
            out_port = self.mac_to_port[dpid][dst]
        else:
            out_port = ofproto.OFPP_FLOOD

        actions = [parser.OFPActionOutput(out_port),
                   parser.OFPActionOutput(self.snort_port)]

        # install a flow to avoid packet_in next time
        if out_port != ofproto.OFPP_FLOOD:
            #match = parser.OFPMatch(in_port=in_port, eth_dst=dst)
           
       	    # check IP Protocol and create a match for IP
            if eth.ethertype == ether_types.ETH_TYPE_IP:
                ip = pkt.get_protocol(ipv4.ipv4)
                srcip = ip.src
                dstip = ip.dst
                match = parser.OFPMatch(eth_type=ether_types.ETH_TYPE_IP, in_port=in_port, eth_src=src, ipv4_src=srcip, ipv4_dst=dstip)                 

		self.logger.info("packet in %s %s %s %s", dpid, srcip, dstip, in_port)     
		
		# verify if we have a valid buffer_id, if yes avoid to send both
            	# flow_mod & packet_out
            	if msg.buffer_id != ofproto.OFP_NO_BUFFER:
                	self.add_flow(datapath, 1, match, actions, msg.buffer_id, idle=10)
                	return
            	else:
                	self.add_flow(datapath, 1, match, actions, idle=10)
		count1 = count1 + 1
		t = timeit.default_timer() - start 
        	self.logger.info("packet-in messages : %s take %s seconds",count1,t)
		packetin.write("\n" + str(count1) + "," + str(t))

        data = None
        if msg.buffer_id == ofproto.OFP_NO_BUFFER:
            data = msg.data

        out = parser.OFPPacketOut(datapath=datapath, buffer_id=msg.buffer_id,
                                  in_port=in_port, actions=actions, data=data)
        datapath.send_msg(out)

    @set_ev_cls(ofp_event.EventOFPFlowStatsReply, MAIN_DISPATCHER)
    def _flow_stats_reply_handler(self, ev):
        body = ev.msg.body
	file = open("FlowStatsfile.txt", "a+")
        self.logger.info('datapath  duration  in_port  eth_src  ip_dst  out-port  packets  bytes')
        self.logger.info('---------------- -------- ----------------- -------- -------- --------')
        for stat in sorted([flow for flow in body if (flow.priority == 1)], key=lambda flow:
        (flow.match['in_port'], flow.match['eth_src'], flow.match['ipv4_dst'])):
            self.logger.info('%016x %8d %8x %17s %17s %8x %8d %8d', ev.msg.datapath.id, stat.duration_sec,
                             stat.match['in_port'], stat.match['eth_src'],
                             stat.match['ipv4_dst'], stat.instructions[0].actions[0].port, stat.packet_count,
                             stat.byte_count)
            file.write("\n" + str(stat.match['eth_src']) + "," + str(stat.duration_sec) + "," + str(stat.packet_count)
                       + "," + str(stat.byte_count))

        file.close()
        

	
