#!/usr/bin/python
import sys
import urllib
import urllib2
import socket
from xml.dom import minidom


class NMA(object):
	def __init__(self, apikey, developerkey=""):
		self.apikey = apikey
		self.developerkey = developerkey

		self.baseurl = "https://www.notifymyandroid.com/publicapi"
		self.verifyurl = self.baseurl + "/verify"
		self.notifyurl = self.baseurl + "/notify"
		self.keyverified = False
	
	def DoRequest(self, url):
		socket.setdefaulttimeout(5)
		resp = urllib2.urlopen(urllib2.Request(url))
		return resp.read()
	
	def parseresult(self, resp):
		dom = minidom.parse(resp)
		error = dom.getElementsByTagName("error")
		if len(error) > 0:
			self.errormsg = error[0].firstChild.data
			return False
		success = dom.getElementsByTagName("success")[0]
		self.remainingcalls = success.attributes['remaining'].value
		self.resettimer = success.attributes['resettimer'].value
		self.successcode = success.attributes['code'].value
		self.keyverified = True
		return True
	
	def verifykey(self):
		self.keyverified = False
		# Length should be 48 characters
		if len(self.apikey) != 48:
			return False

		url = self.verifyurl + "?apikey=" + self.apikey
		resp = urllib2.urlopen(urllib2.Request(url))
		return self.parseresult(resp)
	
	def SendMessage(self, application, event, description, url="", priority = 0, contenttype=""):
		# Parameter		Length		Description
		# application		256		The name of the application that is generating the call.
		#					Example: Nagios
		# 
		# event			1000		The event that is been notified. Depending on your application, it can be a subject or a brief description.
		#					Example: Service is down!
		#
		# description		10000		The notification text. Depending on your application, it can be a body of the message or a full description.
		#					Example: 
		#					Server: 1.2.3.4
		#					Service: mysqld
		#					Status: DOWN
		#					Time of the alert: 1/21/2011 1:32am
		#
		# priority		-		A priority level for this notification. This is optional and in the future will be used to change the way NMA alerts you.
		#					Possible values: -2, -1, 0, 1, 2
		#					-2 = Very Low
		#					-1 = Moderate
		#					0 = Normal
		#					1 = High
		#					2 = Emergency
		#
		# url			2000		An URL/URI can be attached to your notification. You can send URL's or URI's 
		#					supported by your device. The user will be able to long-click the notification
		#					and choose to follow the attached URL/URI, launching the application that can handle it.
		#
		# content-type		-		You can set this parameter to "text/html" while sending the notification, and the basic
		#					html tags below will be interpreted and rendered while displaying the notification:
		#						<a href="...">, <b>, <big>, <blockquote>, <br>, <cite>
		#						<dfn>, <div align="...">, <em>, <font size="..." color="..." face="...">
		#						<h1>, <h2>, <h3>, <h4>, <h5>, <h6>
		#						<i>, <p>, <small>, <strike>, <strong>
		#						<sub>, <sup>, <tt>, <u>

		if len(application) > 256:
			self.errormsg = "application name is too long"
			return False

		if len(event) > 1000:
			self.errormsg = "event name is too long"
			return False

		if type(priority) != int:
			self.errormsg = "priority needs to be an integer"
			return False

		if priority < -2 or priority > 2:
			self.errormsg = "priority needs to be between -2 and 2"
			return False

		if len(url) > 2000:
			self.errormsg = "url has a maximum length of 2000 bytes"
			return False

		options = {
			"application": application,
			"event": event,
			"description": description,
			"priority": priority,
			"apikey": self.apikey
		}

		if len(url) > 0:
			options['url'] = url

		if len(contenttype) > 0:
			options['content-type'] = contenttype

		if len(self.developerkey) > 0:
			options['developerkey'] = self.developerkey


		postdata = urllib.urlencode(options)
		req = urllib2.Request(self.notifyurl, postdata)
		resp = urllib2.urlopen(req)
		return self.parseresult(resp)
	
#MyNMA = NMA("<API KEY>")
#if not MyNMA.verifykey():
#	print "The API key is not valid !"
#	sys.exit(1)
#
#print "API successfully verified"
#print "Calls remaining: " + MyNMA.remainingcalls
#if not MyNMA.SendMessage("my app", "tada", "this is my first message", "http://www.google.com"):
#	print "The following error occured: " + MyNMA.errormsg
#else:
#	print "Message successfully send"
#	print "Messages left: " + MyNMA.remainingcalls
		
