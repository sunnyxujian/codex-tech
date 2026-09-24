from service import create_app

app = create_app('video')

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=8766, debug=False, threaded=True)
